from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path.relative_to(ROOT)}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_block(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


# Canonical transfer evidence must represent an actual offline verification.
transfers = ROOT / "packages/python/prodkit-control-core/src/prodkit_control_core/contracts/transfers.py"
replace_once(
    transfers,
    "from pydantic import AwareDatetime, Field\n",
    "from pydantic import AwareDatetime, Field, model_validator\n",
)
replace_once(
    transfers,
    "    verified_offline: bool = True\n",
    "    verified_offline: bool = True\n\n"
    "    @model_validator(mode=\"after\")\n"
    "    def require_offline_verification(self) -> EvidenceTransferVerification:\n"
    "        if not self.verified_offline:\n"
    "            raise ValueError(\"evidence transfer verification must be offline verified\")\n"
    "        return self\n",
)

# Bind import receipts to the exact verification evidence and add durable deletion-intent events.
contracts = ROOT / "packages/python/prodkit-control-core/src/prodkit_control_core/contracts/governance.py"
replace_once(
    contracts,
    "    RETENTION_EVALUATED = \"retention_evaluated\"\n"
    "    RETENTION_DELETION_EXECUTED = \"retention_deletion_executed\"\n",
    "    RETENTION_EVALUATED = \"retention_evaluated\"\n"
    "    RETENTION_DELETION_INTENT_RECORDED = \"retention_deletion_intent_recorded\"\n"
    "    RETENTION_DELETION_CANCELLED = \"retention_deletion_cancelled\"\n"
    "    RETENTION_DELETION_EXECUTED = \"retention_deletion_executed\"\n",
)
replace_once(
    contracts,
    "    archive_sha256: Sha256\n    verified: bool = True\n",
    "    archive_sha256: Sha256\n"
    "    verification_id: UUID\n"
    "    verification_sha256: Sha256\n"
    "    trust_anchor_sha256: Sha256\n"
    "    verified: bool = True\n",
)

runtime = ROOT / "packages/python/prodkit-control-runtime/src/prodkit_control_runtime/governance.py"
replace_once(
    runtime,
    "    EvidenceImportReceipt,\n    EvidenceTransferManifest,\n",
    "    EvidenceImportReceipt,\n    EvidenceTransferManifest,\n    EvidenceTransferVerification,\n",
)
runtime_execute = '''    async def execute_retention(
        self,
        *,
        context: TenantAccessContext,
        candidates: tuple[RetentionCandidate, ...],
        adapter: RetentionDeletionAdapter,
        at: datetime | None = None,
    ) -> tuple[RetentionExecutionRecord, ...]:
        self._require(context, TenantCapability.DELETE)
        self._require(context, TenantCapability.READ)
        if at is not None:
            raise ValueError(
                "destructive retention execution uses authoritative current time; "
                "caller-supplied evaluation time is not permitted"
            )
        self._require_unique_candidates(candidates)
        decision_at = datetime.now(UTC)
        async with self._lock:
            decisions = self._retention_decisions_locked(
                tenant_id=context.tenant_id,
                candidates=candidates,
                at=decision_at,
            )
            self._audit_decisions_locked(
                context=context, decisions=decisions, occurred_at=decision_at
            )
            records: list[RetentionExecutionRecord] = []
            for candidate, decision in zip(candidates, decisions, strict=True):
                if decision.disposition is not RetentionDisposition.DELETE:
                    continue
                execution_id = uuid4()
                self._append_audit(
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_DELETION_INTENT_RECORDED,
                        actor=context.actor,
                        occurred_at=decision_at,
                        target_id=f"{candidate.resource_type}:{candidate.resource_id}",
                        reason=decision.reason,
                        attributes={
                            "execution_id": str(execution_id),
                            "decision_sha256": sha256_hex(decision),
                            "content_sha256": candidate.content_sha256 or "",
                        },
                    )
                )
                deletion_reference = await adapter.delete(
                    context=context,
                    candidate=candidate,
                    decision=decision,
                )
                record = RetentionExecutionRecord(
                    execution_id=execution_id,
                    tenant_id=context.tenant_id,
                    resource_type=candidate.resource_type,
                    resource_id=candidate.resource_id,
                    executed_at=decision_at,
                    executed_by=context.actor,
                    policy_id=decision.policy_id,
                    policy_revision=decision.policy_revision,
                    content_sha256=candidate.content_sha256,
                    deletion_reference=deletion_reference,
                )
                records.append(record)
                self._append_audit(
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_DELETION_EXECUTED,
                        actor=context.actor,
                        occurred_at=decision_at,
                        target_id=f"{candidate.resource_type}:{candidate.resource_id}",
                        reason=decision.reason,
                        attributes={
                            "execution_id": str(execution_id),
                            "deletion_reference": deletion_reference,
                        },
                    )
                )
            return tuple(records)

'''
replace_block(runtime, "    async def execute_retention(\n", "    async def bootstrap_trust_root(\n", runtime_execute)
runtime_import = '''    async def record_verified_import(
        self,
        *,
        context: TenantAccessContext,
        manifest: EvidenceTransferManifest,
        verification: EvidenceTransferVerification,
        archive_sha256: str,
        compatibility: CompatibilityPolicy,
    ) -> EvidenceImportReceipt:
        self._require(context, TenantCapability.WRITE)
        self._validate_import_verification(
            context=context,
            manifest=manifest,
            verification=verification,
            archive_sha256=archive_sha256,
        )
        compatibility.path_from(manifest.source_schema_version)
        now = datetime.now(UTC)
        receipt = EvidenceImportReceipt(
            import_id=uuid4(),
            transfer_id=manifest.transfer_id,
            tenant_id=context.tenant_id,
            imported_at=now,
            imported_by=context.actor,
            source_control_version=manifest.source_control_version,
            source_schema_version=manifest.source_schema_version,
            archive_sha256=archive_sha256,
            verification_id=verification.verification_id,
            verification_sha256=sha256_hex(verification),
            trust_anchor_sha256=verification.trust_anchor_sha256,
        )
        async with self._lock:
            self._imports[(context.tenant_id, receipt.import_id)] = receipt
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.EVIDENCE_IMPORT_VERIFIED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(receipt.import_id),
                    after_digest=sha256_hex(receipt),
                    reason=context.reason or "verified evidence import",
                    ticket_reference=context.ticket_reference,
                    attributes={
                        "verification_id": str(verification.verification_id),
                        "verification_sha256": sha256_hex(verification),
                        "trust_anchor_sha256": verification.trust_anchor_sha256,
                    },
                )
            )
        return receipt

'''
replace_block(runtime, "    async def record_verified_import(\n", "    async def list_audit(\n", runtime_import)
replace_once(
    runtime,
    "    def _retention_decisions_locked(\n",
    '''    @staticmethod
    def _require_unique_candidates(candidates: tuple[RetentionCandidate, ...]) -> None:
        identities = tuple((candidate.resource_type, candidate.resource_id) for candidate in candidates)
        if len(identities) != len(set(identities)):
            raise ValueError("retention candidates must have unique resource identities")

    @staticmethod
    def _validate_import_verification(
        *,
        context: TenantAccessContext,
        manifest: EvidenceTransferManifest,
        verification: EvidenceTransferVerification,
        archive_sha256: str,
    ) -> None:
        if manifest.tenant_id != context.tenant_id or verification.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("evidence import crossed tenant boundary")
        if verification.transfer_id != manifest.transfer_id:
            raise ValueError("verification does not belong to the transfer manifest")
        if manifest.archive_sha256 != archive_sha256 or verification.package_sha256 != archive_sha256:
            raise ValueError("import archive digest does not match verified transfer evidence")
        if verification.bundle_manifest_sha256 != manifest.bundle_manifest_sha256:
            raise ValueError("verified bundle-manifest digest does not match transfer manifest")
        if verification.source_control_version != manifest.source_control_version:
            raise ValueError("verified source control version does not match transfer manifest")
        if verification.source_schema_version != manifest.source_schema_version:
            raise ValueError("verified source schema version does not match transfer manifest")
        if not verification.verified_offline:
            raise ValueError("evidence import requires offline verification evidence")

    def _retention_decisions_locked(
''',
)

postgres = ROOT / "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/governance.py"
replace_once(
    postgres,
    "    EvidenceImportReceipt,\n    EvidenceTransferManifest,\n",
    "    EvidenceImportReceipt,\n    EvidenceTransferManifest,\n    EvidenceTransferVerification,\n",
)
postgres_execute = '''    async def execute_retention(
        self,
        *,
        context: TenantAccessContext,
        candidates: tuple[RetentionCandidate, ...],
        adapter: RetentionDeletionAdapter,
        at: datetime | None = None,
    ) -> tuple[RetentionExecutionRecord, ...]:
        self._require_tenant_mutation(context, TenantCapability.DELETE)
        self._require_context(context, TenantCapability.READ)
        if at is not None:
            raise ValueError(
                "destructive retention execution uses authoritative database time; "
                "caller-supplied evaluation time is not permitted"
            )
        self._require_unique_candidates(candidates)
        pending: list[tuple[RetentionCandidate, RetentionDecision, UUID]] = []
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            self._require_context(context, TenantCapability.DELETE, at=now)
            self._require_context(context, TenantCapability.READ, at=now)
            policy = await self._current_retention_policy(
                session, context.tenant_id, at=now, include_future=False
            )
            if policy is None:
                raise AuthorizationDeniedError("no effective retention policy is configured")
            holds = await self._active_holds(session, context.tenant_id, for_update=True)
            decisions = tuple(
                self._decision_for(policy=policy, holds=holds, candidate=candidate, at=now)
                for candidate in candidates
            )
            for candidate, decision in zip(candidates, decisions, strict=True):
                await self._append_audit(
                    session,
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_EVALUATED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=f"{decision.resource_type}:{decision.resource_id}",
                        reason=decision.reason,
                        attributes={"disposition": decision.disposition.value},
                    ),
                )
                if decision.disposition is not RetentionDisposition.DELETE:
                    continue
                execution_id = uuid4()
                await self._append_audit(
                    session,
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_DELETION_INTENT_RECORDED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=f"{candidate.resource_type}:{candidate.resource_id}",
                        reason=decision.reason,
                        attributes={
                            "execution_id": str(execution_id),
                            "decision_sha256": sha256_hex(decision),
                            "content_sha256": candidate.content_sha256 or "",
                        },
                    ),
                )
                pending.append((candidate, decision, execution_id))

        records: list[RetentionExecutionRecord] = []
        for candidate, intended_decision, execution_id in pending:
            async with self._sessions.begin() as session:
                await self._tenant_lock(session, context.tenant_id)
                now = await _database_now(session)
                self._require_context(context, TenantCapability.DELETE, at=now)
                self._require_context(context, TenantCapability.READ, at=now)
                policy = await self._current_retention_policy(
                    session, context.tenant_id, at=now, include_future=False
                )
                if policy is None:
                    raise AuthorizationDeniedError("no effective retention policy is configured")
                holds = await self._active_holds(session, context.tenant_id, for_update=True)
                decision = self._decision_for(
                    policy=policy,
                    holds=holds,
                    candidate=candidate,
                    at=now,
                )
                if (
                    decision.disposition is not RetentionDisposition.DELETE
                    or decision.policy_id != intended_decision.policy_id
                    or decision.policy_revision != intended_decision.policy_revision
                ):
                    await self._append_audit(
                        session,
                        GovernanceAuditEvent(
                            event_id=uuid4(),
                            tenant_id=context.tenant_id,
                            event_type=GovernanceAuditEventType.RETENTION_DELETION_CANCELLED,
                            actor=context.actor,
                            occurred_at=now,
                            target_id=f"{candidate.resource_type}:{candidate.resource_id}",
                            reason="deletion intent no longer matches current governed retention state",
                            attributes={"execution_id": str(execution_id)},
                        ),
                    )
                    continue
                deletion_reference = await adapter.delete(
                    context=context,
                    candidate=candidate,
                    decision=decision,
                )
                record = RetentionExecutionRecord(
                    execution_id=execution_id,
                    tenant_id=context.tenant_id,
                    resource_type=candidate.resource_type,
                    resource_id=candidate.resource_id,
                    executed_at=now,
                    executed_by=context.actor,
                    policy_id=decision.policy_id,
                    policy_revision=decision.policy_revision,
                    content_sha256=candidate.content_sha256,
                    deletion_reference=deletion_reference,
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO governance_retention_executions (
                          execution_id, tenant_id, resource_type, resource_id, executed_at,
                          policy_id, policy_revision, document
                        ) VALUES (
                          :execution_id, :tenant_id, :resource_type, :resource_id, :executed_at,
                          :policy_id, :policy_revision, CAST(:document AS JSONB)
                        )
                        """
                    ),
                    {
                        "execution_id": record.execution_id,
                        "tenant_id": record.tenant_id,
                        "resource_type": record.resource_type,
                        "resource_id": record.resource_id,
                        "executed_at": record.executed_at,
                        "policy_id": record.policy_id,
                        "policy_revision": record.policy_revision,
                        "document": record.model_dump_json(),
                    },
                )
                await self._append_audit(
                    session,
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_DELETION_EXECUTED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=f"{candidate.resource_type}:{candidate.resource_id}",
                        reason=decision.reason,
                        attributes={
                            "execution_id": str(execution_id),
                            "deletion_reference": deletion_reference,
                        },
                    ),
                )
                records.append(record)
        return tuple(records)

'''
replace_block(postgres, "    async def execute_retention(\n", "    async def bootstrap_trust_root(\n", postgres_execute)
postgres_import = '''    async def record_verified_import(
        self,
        *,
        context: TenantAccessContext,
        manifest: EvidenceTransferManifest,
        verification: EvidenceTransferVerification,
        archive_sha256: str,
        compatibility: CompatibilityPolicy,
    ) -> EvidenceImportReceipt:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.WRITE, now=now)
            self._validate_import_verification(
                context=context,
                manifest=manifest,
                verification=verification,
                archive_sha256=archive_sha256,
            )
            compatibility.path_from(manifest.source_schema_version)
            receipt = EvidenceImportReceipt(
                import_id=uuid4(),
                transfer_id=manifest.transfer_id,
                tenant_id=context.tenant_id,
                imported_at=now,
                imported_by=context.audited_actor(),
                source_control_version=manifest.source_control_version,
                source_schema_version=manifest.source_schema_version,
                archive_sha256=archive_sha256,
                verification_id=verification.verification_id,
                verification_sha256=sha256_hex(verification),
                trust_anchor_sha256=verification.trust_anchor_sha256,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO governance_evidence_imports (
                      import_id, tenant_id, source_transfer_id, imported_at,
                      archive_sha256, source_schema_version, document
                    ) VALUES (
                      :import_id, :tenant_id, :source_transfer_id, :imported_at,
                      :archive_sha256, :source_schema_version, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "import_id": receipt.import_id,
                    "tenant_id": receipt.tenant_id,
                    "source_transfer_id": receipt.transfer_id,
                    "imported_at": receipt.imported_at,
                    "archive_sha256": receipt.archive_sha256,
                    "source_schema_version": receipt.source_schema_version,
                    "document": receipt.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.EVIDENCE_IMPORT_VERIFIED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    target_id=str(receipt.import_id),
                    after_digest=sha256_hex(receipt),
                    reason=context.reason or "verified evidence import",
                    ticket_reference=context.ticket_reference,
                    attributes={
                        "verification_id": str(verification.verification_id),
                        "verification_sha256": sha256_hex(verification),
                        "trust_anchor_sha256": verification.trust_anchor_sha256,
                    },
                ),
            )
            return receipt

'''
replace_block(postgres, "    async def record_verified_import(\n", "    async def list_audit(\n", postgres_import)
replace_once(
    postgres,
    "    async def _authorize(\n",
    '''    @staticmethod
    def _require_unique_candidates(candidates: tuple[RetentionCandidate, ...]) -> None:
        identities = tuple((candidate.resource_type, candidate.resource_id) for candidate in candidates)
        if len(identities) != len(set(identities)):
            raise ValueError("retention candidates must have unique resource identities")

    @staticmethod
    def _validate_import_verification(
        *,
        context: TenantAccessContext,
        manifest: EvidenceTransferManifest,
        verification: EvidenceTransferVerification,
        archive_sha256: str,
    ) -> None:
        if manifest.tenant_id != context.tenant_id or verification.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("evidence import crossed tenant boundary")
        if verification.transfer_id != manifest.transfer_id:
            raise ValueError("verification does not belong to the transfer manifest")
        if manifest.archive_sha256 != archive_sha256 or verification.package_sha256 != archive_sha256:
            raise ValueError("import archive digest does not match verified transfer evidence")
        if verification.bundle_manifest_sha256 != manifest.bundle_manifest_sha256:
            raise ValueError("verified bundle-manifest digest does not match transfer manifest")
        if verification.source_control_version != manifest.source_control_version:
            raise ValueError("verified source control version does not match transfer manifest")
        if verification.source_schema_version != manifest.source_schema_version:
            raise ValueError("verified source schema version does not match transfer manifest")
        if not verification.verified_offline:
            raise ValueError("evidence import requires offline verification evidence")

    async def _authorize(
''',
)

# TypeScript public-contract parity.
ts = ROOT / "packages/typescript/control/src/index.ts"
replace_once(
    ts,
    '  | "retention_evaluated"\n  | "retention_deletion_executed"\n',
    '  | "retention_evaluated"\n'
    '  | "retention_deletion_intent_recorded"\n'
    '  | "retention_deletion_cancelled"\n'
    '  | "retention_deletion_executed"\n',
)
replace_once(
    ts,
    "  readonly archive_sha256: string;\n  readonly verified: true;\n}\n\nexport interface EvidenceTransferVerification",
    "  readonly archive_sha256: string;\n"
    "  readonly verification_id: string;\n"
    "  readonly verification_sha256: string;\n"
    "  readonly trust_anchor_sha256: string;\n"
    "  readonly verified: true;\n"
    "}\n\nexport interface EvidenceTransferVerification",
)
replace_once(ts, "  readonly verified_offline: boolean;\n", "  readonly verified_offline: true;\n")

# Standalone regression coverage.
tests = ROOT / "tests/test_governance.py"
replace_once(
    tests,
    "    CompatibilityPolicy,\n    GovernanceApprovalDecision,\n",
    "    CompatibilityPolicy,\n    EvidenceTransferVerification,\n    GovernanceApprovalDecision,\n",
)
replace_once(
    tests,
    "    records = await store.execute_retention(\n        context=operator,\n        candidates=(candidate,),\n        adapter=adapter,\n        at=now + timedelta(seconds=1),\n    )\n",
    "    records = await store.execute_retention(\n"
    "        context=operator, candidates=(candidate,), adapter=adapter\n"
    "    )\n",
)
replace_once(
    tests,
    "    receipt = await store.record_verified_import(\n        context=context,\n        manifest=manifest,\n        archive_sha256=\"1\" * 64,\n        compatibility=compatibility,\n    )\n",
    "    verification = EvidenceTransferVerification(\n"
    "        verification_id=uuid4(),\n"
    "        transfer_id=manifest.transfer_id,\n"
    "        tenant_id=manifest.tenant_id,\n"
    "        verified_at=datetime.now(UTC),\n"
    "        source_control_version=manifest.source_control_version,\n"
    "        source_schema_version=manifest.source_schema_version,\n"
    "        package_sha256=manifest.archive_sha256,\n"
    "        bundle_manifest_sha256=manifest.bundle_manifest_sha256,\n"
    "        trust_anchor_sha256=\"4\" * 64,\n"
    "    )\n"
    "    receipt = await store.record_verified_import(\n"
    "        context=context,\n"
    "        manifest=manifest,\n"
    "        verification=verification,\n"
    "        archive_sha256=\"1\" * 64,\n"
    "        compatibility=compatibility,\n"
    "    )\n",
)
replace_once(
    tests,
    "        await store.record_verified_import(\n            context=context,\n            manifest=manifest,\n            archive_sha256=\"3\" * 64,\n            compatibility=compatibility,\n        )\n",
    "        await store.record_verified_import(\n"
    "            context=context,\n"
    "            manifest=manifest,\n"
    "            verification=verification,\n"
    "            archive_sha256=\"3\" * 64,\n"
    "            compatibility=compatibility,\n"
    "        )\n",
)
append_tests = '''

class _FailingDeletionAdapter:
    async def delete(
        self,
        *,
        context: TenantAccessContext,
        candidate: RetentionCandidate,
        decision: RetentionDecision,
    ) -> str:
        raise RuntimeError("simulated provider failure")


@pytest.mark.asyncio
async def test_destructive_retention_rejects_caller_time_and_duplicate_identities() -> None:
    store = InMemoryGovernanceStore()
    operator = _context(
        "tenant-time",
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.READ,
        TenantCapability.DELETE,
    )
    approver = _context("tenant-time", "operator-b", TenantCapability.APPROVE)
    now = datetime.now(UTC)
    policy = RetentionPolicy(
        policy_id=uuid4(),
        tenant_id="tenant-time",
        revision=1,
        effective_at=now,
        rules=(RetentionRule(resource_type="artifact", retain_for_seconds=0),),
        created_at=now,
        created_by=operator.actor,
    )
    request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.RETENTION_POLICY,
        target_id=str(policy.policy_id),
        proposed_digest=sha256_hex(policy),
        risk=GovernanceRisk.HIGH,
        reason="retention safety regression",
        ticket_reference="HOTFIX-1",
    )
    await _approve(store, context=approver, request_id=request.request_id)
    await store.apply_retention_policy(policy, context=operator, request_id=request.request_id)
    candidate = RetentionCandidate(
        tenant_id="tenant-time",
        resource_type="artifact",
        resource_id="same",
        created_at=now - timedelta(days=1),
    )
    with pytest.raises(ValueError, match="authoritative current time"):
        await store.execute_retention(
            context=operator,
            candidates=(candidate,),
            adapter=_DeletionAdapter(),
            at=now + timedelta(days=365),
        )
    with pytest.raises(ValueError, match="unique resource identities"):
        await store.execute_retention(
            context=operator,
            candidates=(candidate, candidate.model_copy()),
            adapter=_DeletionAdapter(),
        )


@pytest.mark.asyncio
async def test_failed_deletion_preserves_intent_evidence() -> None:
    store = InMemoryGovernanceStore()
    operator = _context(
        "tenant-intent",
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.READ,
        TenantCapability.DELETE,
    )
    approver = _context("tenant-intent", "operator-b", TenantCapability.APPROVE)
    now = datetime.now(UTC)
    policy = RetentionPolicy(
        policy_id=uuid4(),
        tenant_id="tenant-intent",
        revision=1,
        effective_at=now,
        rules=(RetentionRule(resource_type="artifact", retain_for_seconds=0),),
        created_at=now,
        created_by=operator.actor,
    )
    request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.RETENTION_POLICY,
        target_id=str(policy.policy_id),
        proposed_digest=sha256_hex(policy),
        risk=GovernanceRisk.HIGH,
        reason="durable deletion intent",
        ticket_reference="HOTFIX-2",
    )
    await _approve(store, context=approver, request_id=request.request_id)
    await store.apply_retention_policy(policy, context=operator, request_id=request.request_id)
    candidate = RetentionCandidate(
        tenant_id="tenant-intent",
        resource_type="artifact",
        resource_id="artifact-fail",
        created_at=now - timedelta(days=1),
    )
    with pytest.raises(RuntimeError, match="simulated provider failure"):
        await store.execute_retention(
            context=operator,
            candidates=(candidate,),
            adapter=_FailingDeletionAdapter(),
        )
    audit = await store.list_audit(context=operator)
    assert any(event.event_type.value == "retention_deletion_intent_recorded" for event in audit)
    assert not any(event.event_type.value == "retention_deletion_executed" for event in audit)


@pytest.mark.asyncio
async def test_import_requires_exact_offline_verification_evidence() -> None:
    store = InMemoryGovernanceStore()
    context = _context(
        "tenant-verify",
        "operator-a",
        TenantCapability.EXPORT,
        TenantCapability.WRITE,
    )
    compatibility = CompatibilityPolicy(current_schema_version=7, minimum_supported_schema_version=7, migration_paths=())
    manifest = await store.create_transfer_manifest(
        context=context,
        source_control_version="0.6.0",
        source_schema_version=7,
        archive_sha256="5" * 64,
        bundle_manifest_sha256="6" * 64,
    )
    verification = EvidenceTransferVerification(
        verification_id=uuid4(),
        transfer_id=manifest.transfer_id,
        tenant_id=manifest.tenant_id,
        verified_at=datetime.now(UTC),
        source_control_version=manifest.source_control_version,
        source_schema_version=manifest.source_schema_version,
        package_sha256=manifest.archive_sha256,
        bundle_manifest_sha256=manifest.bundle_manifest_sha256,
        trust_anchor_sha256="7" * 64,
    )
    receipt = await store.record_verified_import(
        context=context,
        manifest=manifest,
        verification=verification,
        archive_sha256=manifest.archive_sha256,
        compatibility=compatibility,
    )
    assert receipt.verification_id == verification.verification_id
    assert receipt.verification_sha256 == sha256_hex(verification)
    assert receipt.trust_anchor_sha256 == verification.trust_anchor_sha256
    with pytest.raises(ValueError, match="bundle-manifest"):
        await store.record_verified_import(
            context=context,
            manifest=manifest,
            verification=verification.model_copy(update={"bundle_manifest_sha256": "8" * 64}),
            archive_sha256=manifest.archive_sha256,
            compatibility=compatibility,
        )
    with pytest.raises(ValueError, match="offline verified"):
        EvidenceTransferVerification(
            verification_id=uuid4(),
            transfer_id=manifest.transfer_id,
            tenant_id=manifest.tenant_id,
            verified_at=datetime.now(UTC),
            source_control_version=manifest.source_control_version,
            source_schema_version=manifest.source_schema_version,
            package_sha256=manifest.archive_sha256,
            bundle_manifest_sha256=manifest.bundle_manifest_sha256,
            trust_anchor_sha256="9" * 64,
            verified_offline=False,
        )
'''
with tests.open("a", encoding="utf-8") as handle:
    handle.write(append_tests)

# PostgreSQL qualification mirrors the standalone regressions.
pg = ROOT / "scripts/ci_governance_postgres.py"
replace_once(
    pg,
    "    CompatibilityPolicy,\n    GovernanceApprovalDecision,\n",
    "    CompatibilityPolicy,\n    EvidenceTransferVerification,\n    GovernanceApprovalDecision,\n",
)
replace_once(
    pg,
    "    executions = await store.execute_retention(\n        context=operator,\n        candidates=(candidate,),\n        adapter=adapter,\n        at=now + timedelta(seconds=1),\n    )\n",
    "    try:\n"
    "        await store.execute_retention(\n"
    "            context=operator,\n"
    "            candidates=(candidate,),\n"
    "            adapter=adapter,\n"
    "            at=now + timedelta(days=365),\n"
    "        )\n"
    "    except ValueError:\n"
    "        pass\n"
    "    else:\n"
    "        raise AssertionError(\"destructive retention must reject caller-supplied time\")\n"
    "    try:\n"
    "        await store.execute_retention(\n"
    "            context=operator,\n"
    "            candidates=(candidate, candidate.model_copy()),\n"
    "            adapter=adapter,\n"
    "        )\n"
    "    except ValueError:\n"
    "        pass\n"
    "    else:\n"
    "        raise AssertionError(\"duplicate retention candidate identities must fail closed\")\n"
    "    executions = await store.execute_retention(\n"
    "        context=operator, candidates=(candidate,), adapter=adapter\n"
    "    )\n",
)
replace_once(
    pg,
    "    receipt = await store.record_verified_import(\n        context=operator,\n        manifest=transfer,\n        archive_sha256=\"f\" * 64,\n        compatibility=compatibility,\n    )\n",
    "    verification = EvidenceTransferVerification(\n"
    "        verification_id=uuid4(),\n"
    "        transfer_id=transfer.transfer_id,\n"
    "        tenant_id=transfer.tenant_id,\n"
    "        verified_at=datetime.now(UTC),\n"
    "        source_control_version=transfer.source_control_version,\n"
    "        source_schema_version=transfer.source_schema_version,\n"
    "        package_sha256=transfer.archive_sha256,\n"
    "        bundle_manifest_sha256=transfer.bundle_manifest_sha256,\n"
    "        trust_anchor_sha256=\"3\" * 64,\n"
    "    )\n"
    "    receipt = await store.record_verified_import(\n"
    "        context=operator,\n"
    "        manifest=transfer,\n"
    "        verification=verification,\n"
    "        archive_sha256=\"f\" * 64,\n"
    "        compatibility=compatibility,\n"
    "    )\n"
    "    assert receipt.verification_sha256 == sha256_hex(verification)\n",
)

print("v0.6.1 governance safety patch staged")

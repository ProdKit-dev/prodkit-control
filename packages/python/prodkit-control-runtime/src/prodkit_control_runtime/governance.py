from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from prodkit_control_core import (
    AuthorizationDeniedError,
    CompatibilityPolicy,
    EvidenceImportReceipt,
    EvidenceTransferManifest,
    EvidenceTransferVerification,
    GovernanceApproval,
    GovernanceApprovalDecision,
    GovernanceAuditEvent,
    GovernanceAuditEventType,
    GovernanceChangeRequest,
    GovernanceChangeStatus,
    GovernanceRisk,
    GovernanceTargetType,
    GovernedTrustRoot,
    KeyRotationPlan,
    LegalHold,
    LegalHoldStatus,
    RetentionCandidate,
    RetentionDecision,
    RetentionDeletionAdapter,
    RetentionDisposition,
    RetentionExecutionRecord,
    RetentionPolicy,
    SignedCheckpoint,
    TenantAccessContext,
    TenantAccessMode,
    TenantCapability,
    TrustRootHistory,
    TrustRootPolicy,
    sha256_hex,
)

from .attestations import OfflineAssuranceVerifier


def legal_hold_release_digest(*, tenant_id: str, hold_id: UUID) -> str:
    return sha256_hex(
        {
            "schema": "prodkit.legal-hold-release-intent/v1",
            "tenant_id": tenant_id,
            "hold_id": str(hold_id),
        }
    )


class InMemoryGovernanceStore:
    """Standalone governance, retention, trust-root, transfer, and audit control plane.

    The standalone store cannot independently revalidate a support elevation against the live
    tenant-control grant registry, so every governance operation requires ordinary tenant mode.
    Production deployments that need audited support reads use ``PostgresGovernanceStore``, which
    revalidates support opt-in, grant state, actor identity, expiry, and capability on every use.

    Retention execution holds the tenant governance lock while a bounded deletion adapter runs.
    Legal-hold and retention-policy mutations take the same lock, giving hold placement and
    deletion one deterministic serial order instead of a check-then-delete race.
    """

    def __init__(self) -> None:
        self._changes: dict[tuple[str, UUID], GovernanceChangeRequest] = {}
        self._approvals: dict[tuple[str, UUID], list[GovernanceApproval]] = {}
        self._retention: dict[str, list[RetentionPolicy]] = {}
        self._holds: dict[tuple[str, UUID], LegalHold] = {}
        self._trust_roots: dict[str, list[GovernedTrustRoot]] = {}
        self._exports: dict[tuple[str, UUID], EvidenceTransferManifest] = {}
        self._imports: dict[tuple[str, UUID], EvidenceImportReceipt] = {}
        self._audit: dict[str, list[GovernanceAuditEvent]] = {}
        self._lock = asyncio.Lock()

    async def propose_change(
        self,
        *,
        context: TenantAccessContext,
        target_type: GovernanceTargetType,
        target_id: str,
        proposed_digest: str,
        risk: GovernanceRisk,
        reason: str,
        ticket_reference: str,
        expected_current_digest: str | None = None,
    ) -> GovernanceChangeRequest:
        self._require(context, TenantCapability.CONFIGURE)
        now = datetime.now(UTC)
        request = GovernanceChangeRequest(
            request_id=uuid4(),
            tenant_id=context.tenant_id,
            target_type=target_type,
            target_id=target_id,
            proposed_digest=proposed_digest,
            expected_current_digest=expected_current_digest,
            risk=risk,
            reason=reason,
            ticket_reference=ticket_reference,
            proposed_at=now,
            proposed_by=context.actor,
        )
        async with self._lock:
            self._changes[(context.tenant_id, request.request_id)] = request
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.CHANGE_PROPOSED,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=request.request_id,
                    target_type=target_type,
                    target_id=target_id,
                    after_digest=proposed_digest,
                    reason=reason,
                    ticket_reference=ticket_reference,
                    attributes={"risk": risk.value},
                )
            )
        return request

    async def approve_change(
        self,
        *,
        context: TenantAccessContext,
        request_id: UUID,
        decision: GovernanceApprovalDecision,
        reason: str,
    ) -> GovernanceChangeRequest:
        self._require(context, TenantCapability.APPROVE)
        now = datetime.now(UTC)
        async with self._lock:
            request = self._request_locked(context.tenant_id, request_id)
            if request.status is not GovernanceChangeStatus.PROPOSED:
                raise AuthorizationDeniedError("governance change is no longer awaiting approval")
            if request.risk in {GovernanceRisk.HIGH, GovernanceRisk.CRITICAL} and self._same_actor(
                request.proposed_by, context.actor
            ):
                raise AuthorizationDeniedError(
                    "high-risk governance changes require an independent approver"
                )
            approval = GovernanceApproval(
                approval_id=uuid4(),
                request_id=request_id,
                tenant_id=context.tenant_id,
                decision=decision,
                actor=context.actor,
                occurred_at=now,
                reason=reason,
            )
            self._approvals.setdefault((context.tenant_id, request_id), []).append(approval)
            if decision is GovernanceApprovalDecision.REJECT:
                updated = request.model_copy(update={"status": GovernanceChangeStatus.REJECTED})
                event_type = GovernanceAuditEventType.CHANGE_REJECTED
            else:
                updated = request.model_copy(
                    update={
                        "status": GovernanceChangeStatus.APPROVED,
                        "approved_at": now,
                        "approved_by": context.actor,
                    }
                )
                event_type = GovernanceAuditEventType.CHANGE_APPROVED
            self._changes[(context.tenant_id, request_id)] = updated
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=event_type,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=request_id,
                    target_type=request.target_type,
                    target_id=request.target_id,
                    after_digest=request.proposed_digest,
                    reason=reason,
                    ticket_reference=request.ticket_reference,
                )
            )
            return updated

    async def apply_retention_policy(
        self,
        policy: RetentionPolicy,
        *,
        context: TenantAccessContext,
        request_id: UUID,
    ) -> RetentionPolicy:
        self._require(context, TenantCapability.CONFIGURE)
        if policy.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("retention policy crossed tenant boundary")
        now = datetime.now(UTC)
        async with self._lock:
            history = self._retention.setdefault(context.tenant_id, [])
            current = history[-1] if history else None
            expected_revision = 1 if current is None else current.revision + 1
            if policy.revision != expected_revision:
                raise ValueError(f"retention policy revision must advance to {expected_revision}")
            before_digest = sha256_hex(current) if current is not None else None
            digest = sha256_hex(policy)
            request = self._approved_request_locked(
                tenant_id=context.tenant_id,
                request_id=request_id,
                target_type=GovernanceTargetType.RETENTION_POLICY,
                target_id=str(policy.policy_id),
                proposed_digest=digest,
                current_digest=before_digest,
            )
            history.append(policy)
            self._mark_applied_locked(request, at=now)
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.CHANGE_APPLIED,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=request_id,
                    target_type=GovernanceTargetType.RETENTION_POLICY,
                    target_id=str(policy.policy_id),
                    before_digest=before_digest,
                    after_digest=digest,
                    reason=request.reason,
                    ticket_reference=request.ticket_reference,
                    attributes={"revision": str(policy.revision)},
                )
            )
            return policy

    async def current_retention_policy(
        self,
        *,
        context: TenantAccessContext,
        at: datetime | None = None,
    ) -> RetentionPolicy | None:
        self._require(context, TenantCapability.READ)
        now = at or datetime.now(UTC)
        async with self._lock:
            return self._effective_policy_locked(context.tenant_id, now)

    async def place_legal_hold(
        self,
        *,
        context: TenantAccessContext,
        hold: LegalHold,
    ) -> LegalHold:
        self._require(context, TenantCapability.LEGAL_HOLD)
        if hold.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("legal hold crossed tenant boundary")
        if hold.status is not LegalHoldStatus.ACTIVE:
            raise ValueError("only active legal holds can be placed")
        async with self._lock:
            key = (context.tenant_id, hold.hold_id)
            if key in self._holds:
                raise ValueError("legal hold already exists")
            self._holds[key] = hold
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.LEGAL_HOLD_PLACED,
                    actor=context.actor,
                    occurred_at=hold.placed_at,
                    target_id=str(hold.hold_id),
                    reason=hold.reason,
                    ticket_reference=hold.case_reference,
                )
            )
            return hold

    async def propose_legal_hold_release(
        self,
        *,
        context: TenantAccessContext,
        hold_id: UUID,
        reason: str,
        ticket_reference: str,
    ) -> GovernanceChangeRequest:
        self._require(context, TenantCapability.CONFIGURE)
        async with self._lock:
            hold = self._holds.get((context.tenant_id, hold_id))
            if hold is None or hold.status is not LegalHoldStatus.ACTIVE:
                raise KeyError(hold_id)
        return await self.propose_change(
            context=context,
            target_type=GovernanceTargetType.LEGAL_HOLD_RELEASE,
            target_id=str(hold_id),
            proposed_digest=legal_hold_release_digest(
                tenant_id=context.tenant_id,
                hold_id=hold_id,
            ),
            risk=GovernanceRisk.CRITICAL,
            reason=reason,
            ticket_reference=ticket_reference,
        )

    async def release_legal_hold(
        self,
        *,
        context: TenantAccessContext,
        hold_id: UUID,
        request_id: UUID,
    ) -> LegalHold:
        self._require(context, TenantCapability.LEGAL_HOLD)
        now = datetime.now(UTC)
        async with self._lock:
            key = (context.tenant_id, hold_id)
            hold = self._holds.get(key)
            if hold is None or hold.status is not LegalHoldStatus.ACTIVE:
                raise KeyError(hold_id)
            digest = legal_hold_release_digest(tenant_id=context.tenant_id, hold_id=hold_id)
            request = self._approved_request_locked(
                tenant_id=context.tenant_id,
                request_id=request_id,
                target_type=GovernanceTargetType.LEGAL_HOLD_RELEASE,
                target_id=str(hold_id),
                proposed_digest=digest,
                current_digest=None,
            )
            released = hold.model_copy(
                update={
                    "status": LegalHoldStatus.RELEASED,
                    "released_at": now,
                    "released_by": context.actor,
                    "release_change_request_id": request_id,
                }
            )
            self._holds[key] = released
            self._mark_applied_locked(request, at=now)
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.LEGAL_HOLD_RELEASED,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=request_id,
                    target_type=GovernanceTargetType.LEGAL_HOLD_RELEASE,
                    target_id=str(hold_id),
                    after_digest=digest,
                    reason=request.reason,
                    ticket_reference=request.ticket_reference,
                )
            )
            return released

    async def evaluate_retention(
        self,
        *,
        context: TenantAccessContext,
        candidates: tuple[RetentionCandidate, ...],
        at: datetime | None = None,
    ) -> tuple[RetentionDecision, ...]:
        self._require(context, TenantCapability.READ)
        decision_at = at or datetime.now(UTC)
        async with self._lock:
            decisions = self._retention_decisions_locked(
                tenant_id=context.tenant_id,
                candidates=candidates,
                at=decision_at,
            )
            self._audit_decisions_locked(
                context=context, decisions=decisions, occurred_at=decision_at
            )
            return decisions

    async def execute_retention(
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

    async def bootstrap_trust_root(
        self,
        *,
        context: TenantAccessContext,
        policy: TrustRootPolicy,
        activated_at: datetime,
        request_id: UUID,
    ) -> GovernedTrustRoot:
        self._require(context, TenantCapability.CONFIGURE)
        digest = sha256_hex(policy)
        async with self._lock:
            if self._trust_roots.get(context.tenant_id):
                raise ValueError("trust-root history is already initialized")
            request = self._approved_request_locked(
                tenant_id=context.tenant_id,
                request_id=request_id,
                target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
                target_id=policy.policy_id,
                proposed_digest=digest,
                current_digest=None,
            )
            root = GovernedTrustRoot(
                tenant_id=context.tenant_id,
                revision=1,
                policy=policy,
                policy_sha256=digest,
                activated_at=activated_at,
                change_request_id=request_id,
            )
            self._trust_roots[context.tenant_id] = [root]
            now = datetime.now(UTC)
            self._mark_applied_locked(request, at=now)
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.TRUST_ROOT_ACTIVATED,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=request_id,
                    target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
                    target_id=policy.policy_id,
                    after_digest=digest,
                    reason=request.reason,
                    ticket_reference=request.ticket_reference,
                    attributes={"revision": "1"},
                )
            )
            return root

    async def rotate_trust_root(
        self,
        *,
        context: TenantAccessContext,
        plan: KeyRotationPlan,
        policy: TrustRootPolicy,
    ) -> TrustRootHistory:
        self._require(context, TenantCapability.CONFIGURE)
        if plan.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("trust-root rotation crossed tenant boundary")
        now = datetime.now(UTC)
        if plan.activate_at < now and not plan.emergency:
            raise ValueError("non-emergency key rotation cannot activate in the past")
        digest = sha256_hex(policy)
        async with self._lock:
            roots = self._trust_roots.setdefault(context.tenant_id, [])
            current = roots[-1] if roots else None
            if current is None:
                raise ValueError("trust-root rotation requires an initialized trust root")
            if current.revision != plan.from_revision or plan.to_revision != current.revision + 1:
                raise ValueError("key rotation revisions do not match current trust root")
            request = self._approved_request_locked(
                tenant_id=context.tenant_id,
                request_id=plan.change_request_id,
                target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
                target_id=policy.policy_id,
                proposed_digest=digest,
                current_digest=current.policy_sha256,
            )
            roots[-1] = current.model_copy(update={"retired_at": plan.overlap_until})
            roots.append(
                GovernedTrustRoot(
                    tenant_id=context.tenant_id,
                    revision=plan.to_revision,
                    policy=policy,
                    policy_sha256=digest,
                    activated_at=plan.activate_at,
                    change_request_id=plan.change_request_id,
                )
            )
            self._mark_applied_locked(request, at=now)
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.TRUST_ROOT_ACTIVATED,
                    actor=context.actor,
                    occurred_at=now,
                    request_id=plan.change_request_id,
                    target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
                    target_id=policy.policy_id,
                    before_digest=current.policy_sha256,
                    after_digest=digest,
                    reason=request.reason,
                    ticket_reference=request.ticket_reference,
                    attributes={
                        "from_revision": str(plan.from_revision),
                        "to_revision": str(plan.to_revision),
                        "overlap_until": plan.overlap_until.isoformat(),
                    },
                )
            )
            return TrustRootHistory(tenant_id=context.tenant_id, roots=tuple(roots))

    async def trust_root_history(self, *, context: TenantAccessContext) -> TrustRootHistory:
        self._require(context, TenantCapability.READ)
        async with self._lock:
            return TrustRootHistory(
                tenant_id=context.tenant_id,
                roots=tuple(self._trust_roots.get(context.tenant_id, ())),
            )

    @staticmethod
    def verify_checkpoint_history(
        checkpoint: SignedCheckpoint,
        *,
        history: TrustRootHistory,
    ) -> None:
        if checkpoint.tenant_id != history.tenant_id:
            raise AuthorizationDeniedError("checkpoint tenant does not match trust-root history")
        policy = history.policy_for(checkpoint.created_at, key_id=checkpoint.key_id)
        OfflineAssuranceVerifier.verify_checkpoint(checkpoint, trust_policy=policy)

    async def create_transfer_manifest(
        self,
        *,
        context: TenantAccessContext,
        source_control_version: str,
        source_schema_version: int,
        archive_sha256: str,
        bundle_manifest_sha256: str,
        trust_root_revision: int | None = None,
    ) -> EvidenceTransferManifest:
        self._require(context, TenantCapability.EXPORT)
        now = datetime.now(UTC)
        manifest = EvidenceTransferManifest(
            transfer_id=uuid4(),
            tenant_id=context.tenant_id,
            created_at=now,
            created_by=context.actor,
            source_control_version=source_control_version,
            source_schema_version=source_schema_version,
            archive_sha256=archive_sha256,
            bundle_manifest_sha256=bundle_manifest_sha256,
            trust_root_revision=trust_root_revision,
            legal_hold_preserved=True,
        )
        async with self._lock:
            self._exports[(context.tenant_id, manifest.transfer_id)] = manifest
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.EVIDENCE_EXPORT_CREATED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(manifest.transfer_id),
                    after_digest=sha256_hex(manifest),
                    reason=context.reason or "evidence export",
                    ticket_reference=context.ticket_reference,
                )
            )
        return manifest

    async def record_verified_import(
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

    async def list_audit(
        self,
        *,
        context: TenantAccessContext,
    ) -> tuple[GovernanceAuditEvent, ...]:
        self._require(context, TenantCapability.READ)
        async with self._lock:
            return tuple(self._audit.get(context.tenant_id, ()))

    @staticmethod
    def _require_unique_candidates(candidates: tuple[RetentionCandidate, ...]) -> None:
        identities = tuple(
            (candidate.resource_type, candidate.resource_id) for candidate in candidates
        )
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
        if (
            manifest.archive_sha256 != archive_sha256
            or verification.package_sha256 != archive_sha256
        ):
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
        self,
        *,
        tenant_id: str,
        candidates: tuple[RetentionCandidate, ...],
        at: datetime,
    ) -> tuple[RetentionDecision, ...]:
        policy = self._effective_policy_locked(tenant_id, at)
        if policy is None:
            raise AuthorizationDeniedError("no effective retention policy is configured")
        holds = tuple(
            hold
            for (hold_tenant, _), hold in self._holds.items()
            if hold_tenant == tenant_id and hold.status is LegalHoldStatus.ACTIVE
        )
        return tuple(
            self._decision_for(policy=policy, holds=holds, candidate=candidate, at=at)
            for candidate in candidates
        )

    def _audit_decisions_locked(
        self,
        *,
        context: TenantAccessContext,
        decisions: tuple[RetentionDecision, ...],
        occurred_at: datetime,
    ) -> None:
        for decision in decisions:
            self._append_audit(
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.RETENTION_EVALUATED,
                    actor=context.actor,
                    occurred_at=occurred_at,
                    target_id=f"{decision.resource_type}:{decision.resource_id}",
                    reason=decision.reason,
                    attributes={"disposition": decision.disposition.value},
                )
            )

    def _effective_policy_locked(self, tenant_id: str, at: datetime) -> RetentionPolicy | None:
        eligible = tuple(
            policy for policy in self._retention.get(tenant_id, ()) if policy.effective_at <= at
        )
        return eligible[-1] if eligible else None

    @staticmethod
    def _decision_for(
        *,
        policy: RetentionPolicy,
        holds: tuple[LegalHold, ...],
        candidate: RetentionCandidate,
        at: datetime,
    ) -> RetentionDecision:
        if candidate.tenant_id != policy.tenant_id:
            raise AuthorizationDeniedError("retention candidate crossed tenant boundary")
        matching_holds = tuple(hold.hold_id for hold in holds if hold.applies_to(candidate))
        if matching_holds:
            return RetentionDecision(
                tenant_id=candidate.tenant_id,
                resource_type=candidate.resource_type,
                resource_id=candidate.resource_id,
                disposition=RetentionDisposition.RETAIN,
                evaluated_at=at,
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                legal_hold_ids=matching_holds,
                reason="active legal hold takes precedence over deletion policy",
            )
        rule = policy.rule_for(candidate.resource_type)
        if rule is not None and not rule.deletion_allowed:
            return RetentionDecision(
                tenant_id=candidate.tenant_id,
                resource_type=candidate.resource_type,
                resource_id=candidate.resource_id,
                disposition=RetentionDisposition.RETAIN,
                evaluated_at=at,
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                reason="resource type is configured as non-deletable",
            )
        retain_for = (
            rule.retain_for_seconds if rule is not None else policy.default_retain_for_seconds
        )
        grace = rule.deletion_grace_seconds if rule is not None else 0
        if retain_for is None:
            return RetentionDecision(
                tenant_id=candidate.tenant_id,
                resource_type=candidate.resource_type,
                resource_id=candidate.resource_id,
                disposition=RetentionDisposition.RETAIN,
                evaluated_at=at,
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                reason="retention policy keeps this resource indefinitely",
            )
        delete_not_before = candidate.created_at + timedelta(seconds=retain_for + grace)
        if at < delete_not_before:
            return RetentionDecision(
                tenant_id=candidate.tenant_id,
                resource_type=candidate.resource_type,
                resource_id=candidate.resource_id,
                disposition=RetentionDisposition.RETAIN,
                evaluated_at=at,
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                reason="retention window has not elapsed",
            )
        return RetentionDecision(
            tenant_id=candidate.tenant_id,
            resource_type=candidate.resource_type,
            resource_id=candidate.resource_id,
            disposition=RetentionDisposition.DELETE,
            evaluated_at=at,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            delete_not_before=delete_not_before,
            reason="retention and deletion-grace windows elapsed",
        )

    def _request_locked(self, tenant_id: str, request_id: UUID) -> GovernanceChangeRequest:
        request = self._changes.get((tenant_id, request_id))
        if request is None:
            raise KeyError(request_id)
        return request

    def _approved_request_locked(
        self,
        *,
        tenant_id: str,
        request_id: UUID,
        target_type: GovernanceTargetType,
        target_id: str,
        proposed_digest: str,
        current_digest: str | None,
    ) -> GovernanceChangeRequest:
        request = self._request_locked(tenant_id, request_id)
        if request.status is not GovernanceChangeStatus.APPROVED:
            raise AuthorizationDeniedError("governance change is not approved")
        if (
            request.target_type is not target_type
            or request.target_id != target_id
            or request.proposed_digest != proposed_digest
        ):
            raise AuthorizationDeniedError("approved governance change does not match payload")
        if request.expected_current_digest != current_digest:
            raise AuthorizationDeniedError("governance change lost optimistic-concurrency race")
        return request

    def _mark_applied_locked(
        self,
        request: GovernanceChangeRequest,
        *,
        at: datetime,
    ) -> GovernanceChangeRequest:
        applied = request.model_copy(
            update={"status": GovernanceChangeStatus.APPLIED, "applied_at": at}
        )
        self._changes[(request.tenant_id, request.request_id)] = applied
        return applied

    def _append_audit(self, event: GovernanceAuditEvent) -> None:
        self._audit.setdefault(event.tenant_id, []).append(event)

    @staticmethod
    def _same_actor(left: object, right: object) -> bool:
        return (
            getattr(left, "kind", None) == getattr(right, "kind", None)
            and getattr(left, "id", None) == getattr(right, "id", None)
            and getattr(left, "tenant_id", None) == getattr(right, "tenant_id", None)
        )

    @staticmethod
    def _require(context: TenantAccessContext, capability: TenantCapability) -> None:
        if context.mode is not TenantAccessMode.TENANT:
            raise AuthorizationDeniedError(
                "standalone governance cannot use support elevation without live grant revalidation"
            )
        try:
            context.require(capability)
        except PermissionError as exc:
            raise AuthorizationDeniedError(str(exc)) from exc

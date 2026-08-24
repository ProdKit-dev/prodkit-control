from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    ActorRef,
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
    SupportElevationGrant,
    TenantAccessContext,
    TenantAccessMode,
    TenantCapability,
    TenantIsolationProfile,
    TrustRootHistory,
    TrustRootPolicy,
    sha256_hex,
)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL did not return an aware database timestamp")
    return value


class PostgresGovernanceStore:
    """Durable governance and lifecycle control plane for the enterprise profile.

    Mutations require an ordinary tenant context: support elevation is intentionally incapable of
    approving or changing retention, legal holds, trust roots, or other governance policy. Read and
    transfer operations may use support elevation only after the live v0.5 grant is revalidated.

    Retention execution holds a tenant-scoped PostgreSQL advisory transaction lock while the
    bounded deletion adapter runs. Legal-hold and retention-policy mutations take the same lock,
    establishing a linearizable ordering between a committed hold and deletion authorization.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

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
        self._require_tenant_mutation(context, TenantCapability.CONFIGURE)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
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
            await session.execute(
                text(
                    """
                    INSERT INTO governance_change_requests (
                      tenant_id, request_id, target_type, target_id, proposed_digest,
                      expected_current_digest, risk, status, proposed_at, approved_at,
                      applied_at, document
                    ) VALUES (
                      :tenant_id, :request_id, :target_type, :target_id, :proposed_digest,
                      :expected_current_digest, :risk, :status, :proposed_at, NULL,
                      NULL, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": request.tenant_id,
                    "request_id": request.request_id,
                    "target_type": request.target_type.value,
                    "target_id": request.target_id,
                    "proposed_digest": request.proposed_digest,
                    "expected_current_digest": request.expected_current_digest,
                    "risk": request.risk.value,
                    "status": request.status.value,
                    "proposed_at": request.proposed_at,
                    "document": request.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=request.tenant_id,
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
                ),
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
        self._require_tenant_mutation(context, TenantCapability.APPROVE)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            request = await self._request(session, context.tenant_id, request_id, for_update=True)
            if request is None:
                raise KeyError(request_id)
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
            await session.execute(
                text(
                    """
                    INSERT INTO governance_approvals (
                      approval_id, tenant_id, request_id, decision, occurred_at, document
                    ) VALUES (
                      :approval_id, :tenant_id, :request_id, :decision, :occurred_at,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "approval_id": approval.approval_id,
                    "tenant_id": approval.tenant_id,
                    "request_id": approval.request_id,
                    "decision": approval.decision.value,
                    "occurred_at": approval.occurred_at,
                    "document": approval.model_dump_json(),
                },
            )
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
            await self._save_request(session, updated)
            await self._append_audit(
                session,
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
                ),
            )
            return updated

    async def apply_retention_policy(
        self,
        policy: RetentionPolicy,
        *,
        context: TenantAccessContext,
        request_id: UUID,
    ) -> RetentionPolicy:
        self._require_tenant_mutation(context, TenantCapability.CONFIGURE)
        if policy.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("retention policy crossed tenant boundary")
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            current = await self._current_retention_policy(
                session, context.tenant_id, at=now, include_future=True, for_update=True
            )
            expected_revision = 1 if current is None else current.revision + 1
            if policy.revision != expected_revision:
                raise ValueError(f"retention policy revision must advance to {expected_revision}")
            before_digest = sha256_hex(current) if current is not None else None
            digest = sha256_hex(policy)
            request = await self._approved_request(
                session,
                tenant_id=context.tenant_id,
                request_id=request_id,
                target_type=GovernanceTargetType.RETENTION_POLICY,
                target_id=str(policy.policy_id),
                proposed_digest=digest,
                current_digest=before_digest,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO governance_retention_policies (
                      tenant_id, policy_id, revision, effective_at, policy_sha256, document
                    ) VALUES (
                      :tenant_id, :policy_id, :revision, :effective_at, :policy_sha256,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": policy.tenant_id,
                    "policy_id": policy.policy_id,
                    "revision": policy.revision,
                    "effective_at": policy.effective_at,
                    "policy_sha256": digest,
                    "document": policy.model_dump_json(),
                },
            )
            await self._mark_applied(session, request, at=now)
            await self._append_audit(
                session,
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
                ),
            )
            return policy

    async def current_retention_policy(
        self,
        *,
        context: TenantAccessContext,
        at: datetime | None = None,
    ) -> RetentionPolicy | None:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            return await self._current_retention_policy(
                session, context.tenant_id, at=at or now, include_future=False
            )

    async def place_legal_hold(
        self,
        *,
        context: TenantAccessContext,
        hold: LegalHold,
    ) -> LegalHold:
        self._require_tenant_mutation(context, TenantCapability.LEGAL_HOLD)
        if hold.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("legal hold crossed tenant boundary")
        if hold.status is not LegalHoldStatus.ACTIVE:
            raise ValueError("only active legal holds can be placed")
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO governance_legal_holds (
                      tenant_id, hold_id, status, placed_at, released_at, document
                    ) VALUES (
                      :tenant_id, :hold_id, :status, :placed_at, NULL, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": hold.tenant_id,
                    "hold_id": hold.hold_id,
                    "status": hold.status.value,
                    "placed_at": hold.placed_at,
                    "document": hold.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.LEGAL_HOLD_PLACED,
                    actor=context.actor,
                    occurred_at=hold.placed_at,
                    target_id=str(hold.hold_id),
                    reason=hold.reason,
                    ticket_reference=hold.case_reference,
                ),
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
        self._require_tenant_mutation(context, TenantCapability.CONFIGURE)
        async with self._sessions() as session:
            hold = await self._hold(session, context.tenant_id, hold_id)
            if hold is None or hold.status is not LegalHoldStatus.ACTIVE:
                raise KeyError(hold_id)
        return await self.propose_change(
            context=context,
            target_type=GovernanceTargetType.LEGAL_HOLD_RELEASE,
            target_id=str(hold_id),
            proposed_digest=self.legal_hold_release_digest(
                tenant_id=context.tenant_id, hold_id=hold_id
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
        self._require_tenant_mutation(context, TenantCapability.LEGAL_HOLD)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            hold = await self._hold(session, context.tenant_id, hold_id, for_update=True)
            if hold is None or hold.status is not LegalHoldStatus.ACTIVE:
                raise KeyError(hold_id)
            digest = self.legal_hold_release_digest(tenant_id=context.tenant_id, hold_id=hold_id)
            request = await self._approved_request(
                session,
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
            await session.execute(
                text(
                    """
                    UPDATE governance_legal_holds
                    SET status = :status, released_at = :released_at, document = CAST(:document AS JSONB)
                    WHERE tenant_id = :tenant_id AND hold_id = :hold_id
                    """
                ),
                {
                    "status": released.status.value,
                    "released_at": released.released_at,
                    "document": released.model_dump_json(),
                    "tenant_id": released.tenant_id,
                    "hold_id": released.hold_id,
                },
            )
            await self._mark_applied(session, request, at=now)
            await self._append_audit(
                session,
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
                ),
            )
            return released

    async def evaluate_retention(
        self,
        *,
        context: TenantAccessContext,
        candidates: tuple[RetentionCandidate, ...],
        at: datetime | None = None,
    ) -> tuple[RetentionDecision, ...]:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            decision_at = at or now
            policy = await self._current_retention_policy(
                session, context.tenant_id, at=decision_at, include_future=False
            )
            if policy is None:
                raise AuthorizationDeniedError("no effective retention policy is configured")
            holds = await self._active_holds(session, context.tenant_id)
            decisions = tuple(
                self._decision_for(policy=policy, holds=holds, candidate=candidate, at=decision_at)
                for candidate in candidates
            )
            for decision in decisions:
                await self._append_audit(
                    session,
                    GovernanceAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=GovernanceAuditEventType.RETENTION_EVALUATED,
                        actor=context.audited_actor(),
                        occurred_at=now,
                        target_id=f"{decision.resource_type}:{decision.resource_id}",
                        reason=decision.reason,
                        attributes={"disposition": decision.disposition.value},
                    ),
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

    async def bootstrap_trust_root(
        self,
        *,
        context: TenantAccessContext,
        policy: TrustRootPolicy,
        activated_at: datetime,
        request_id: UUID,
    ) -> GovernedTrustRoot:
        self._require_tenant_mutation(context, TenantCapability.CONFIGURE)
        digest = sha256_hex(policy)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            current = await self._current_trust_root(session, context.tenant_id, for_update=True)
            if current is not None:
                raise ValueError("trust-root history is already initialized")
            request = await self._approved_request(
                session,
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
            await self._insert_trust_root(session, root)
            await self._mark_applied(session, request, at=now)
            await self._append_audit(
                session,
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
                ),
            )
            return root

    async def rotate_trust_root(
        self,
        *,
        context: TenantAccessContext,
        plan: KeyRotationPlan,
        policy: TrustRootPolicy,
    ) -> TrustRootHistory:
        self._require_tenant_mutation(context, TenantCapability.CONFIGURE)
        if plan.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("trust-root rotation crossed tenant boundary")
        digest = sha256_hex(policy)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            if plan.activate_at < now and not plan.emergency:
                raise ValueError("non-emergency key rotation cannot activate in the past")
            current = await self._current_trust_root(session, context.tenant_id, for_update=True)
            if current is None:
                raise ValueError("trust-root rotation requires an initialized trust root")
            if current.revision != plan.from_revision or plan.to_revision != current.revision + 1:
                raise ValueError("key rotation revisions do not match current trust root")
            request = await self._approved_request(
                session,
                tenant_id=context.tenant_id,
                request_id=plan.change_request_id,
                target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
                target_id=policy.policy_id,
                proposed_digest=digest,
                current_digest=current.policy_sha256,
            )
            retired = current.model_copy(update={"retired_at": plan.overlap_until})
            await session.execute(
                text(
                    """
                    UPDATE governance_trust_roots
                    SET retired_at = :retired_at, document = CAST(:document AS JSONB)
                    WHERE tenant_id = :tenant_id AND revision = :revision
                    """
                ),
                {
                    "retired_at": retired.retired_at,
                    "document": retired.model_dump_json(),
                    "tenant_id": retired.tenant_id,
                    "revision": retired.revision,
                },
            )
            next_root = GovernedTrustRoot(
                tenant_id=context.tenant_id,
                revision=plan.to_revision,
                policy=policy,
                policy_sha256=digest,
                activated_at=plan.activate_at,
                change_request_id=plan.change_request_id,
            )
            await self._insert_trust_root(session, next_root)
            await self._mark_applied(session, request, at=now)
            await self._append_audit(
                session,
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
                ),
            )
            return await self._trust_root_history(session, context.tenant_id)

    async def trust_root_history(
        self,
        *,
        context: TenantAccessContext,
    ) -> TrustRootHistory:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            return await self._trust_root_history(session, context.tenant_id)

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
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.EXPORT, now=now)
            manifest = EvidenceTransferManifest(
                transfer_id=uuid4(),
                tenant_id=context.tenant_id,
                created_at=now,
                created_by=context.audited_actor(),
                source_control_version=source_control_version,
                source_schema_version=source_schema_version,
                archive_sha256=archive_sha256,
                bundle_manifest_sha256=bundle_manifest_sha256,
                trust_root_revision=trust_root_revision,
                legal_hold_preserved=True,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO governance_evidence_transfers (
                      tenant_id, transfer_id, created_at, archive_sha256, document
                    ) VALUES (
                      :tenant_id, :transfer_id, :created_at, :archive_sha256,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": manifest.tenant_id,
                    "transfer_id": manifest.transfer_id,
                    "created_at": manifest.created_at,
                    "archive_sha256": manifest.archive_sha256,
                    "document": manifest.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                GovernanceAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=GovernanceAuditEventType.EVIDENCE_EXPORT_CREATED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    target_id=str(manifest.transfer_id),
                    after_digest=sha256_hex(manifest),
                    reason=context.reason or "evidence export",
                    ticket_reference=context.ticket_reference,
                ),
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

    async def list_audit(
        self,
        *,
        context: TenantAccessContext,
    ) -> tuple[GovernanceAuditEvent, ...]:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            rows = (
                (
                    await session.execute(
                        text(
                            """
                            SELECT document
                            FROM governance_audit_events
                            WHERE tenant_id = :tenant_id
                            ORDER BY occurred_at, event_id
                            """
                        ),
                        {"tenant_id": context.tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            return tuple(GovernanceAuditEvent.model_validate(row["document"]) for row in rows)

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

    async def _authorize(
        self,
        session: AsyncSession,
        context: TenantAccessContext,
        capability: TenantCapability,
        *,
        now: datetime,
    ) -> None:
        self._require_context(context, capability, at=now)
        if context.mode is TenantAccessMode.TENANT:
            return
        if context.elevation_id is None:
            raise AuthorizationDeniedError("support access has no elevation identity")
        profile = await self._tenant_profile(session, context.tenant_id)
        grant = await self._support_grant(session, context.tenant_id, context.elevation_id)
        if profile is None or not profile.allow_support_access:
            raise AuthorizationDeniedError("target tenant has disabled support elevation")
        if (
            grant is None
            or not grant.active(at=now)
            or not self._same_actor(grant.operator, context.actor)
            or capability not in grant.capabilities
            or grant.reason != context.reason
            or grant.ticket_reference != context.ticket_reference
        ):
            raise AuthorizationDeniedError("support elevation no longer authorizes this operation")

    @staticmethod
    def _require_tenant_mutation(
        context: TenantAccessContext,
        capability: TenantCapability,
    ) -> None:
        if context.mode is not TenantAccessMode.TENANT:
            raise AuthorizationDeniedError("support elevation cannot mutate governance state")
        PostgresGovernanceStore._require_context(context, capability)

    @staticmethod
    def _require_context(
        context: TenantAccessContext,
        capability: TenantCapability,
        *,
        at: datetime | None = None,
    ) -> None:
        try:
            context.require(capability, at=at or datetime.now(UTC))
        except PermissionError as exc:
            raise AuthorizationDeniedError(str(exc)) from exc

    @staticmethod
    async def _tenant_lock(session: AsyncSession, tenant_id: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"prodkit-governance:{tenant_id}"},
        )

    async def _request(
        self,
        session: AsyncSession,
        tenant_id: str,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> GovernanceChangeRequest | None:
        statement = (
            text(
                "SELECT document FROM governance_change_requests "
                "WHERE tenant_id = :tenant_id AND request_id = :request_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT document FROM governance_change_requests "
                "WHERE tenant_id = :tenant_id AND request_id = :request_id"
            )
        )
        row = (
            (await session.execute(statement, {"tenant_id": tenant_id, "request_id": request_id}))
            .mappings()
            .first()
        )
        return GovernanceChangeRequest.model_validate(row["document"]) if row is not None else None

    async def _approved_request(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        request_id: UUID,
        target_type: GovernanceTargetType,
        target_id: str,
        proposed_digest: str,
        current_digest: str | None,
    ) -> GovernanceChangeRequest:
        request = await self._request(session, tenant_id, request_id, for_update=True)
        if request is None:
            raise KeyError(request_id)
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

    async def _save_request(
        self,
        session: AsyncSession,
        request: GovernanceChangeRequest,
    ) -> None:
        await session.execute(
            text(
                """
                UPDATE governance_change_requests
                SET status = :status, approved_at = :approved_at, applied_at = :applied_at,
                    document = CAST(:document AS JSONB)
                WHERE tenant_id = :tenant_id AND request_id = :request_id
                """
            ),
            {
                "status": request.status.value,
                "approved_at": request.approved_at,
                "applied_at": request.applied_at,
                "document": request.model_dump_json(),
                "tenant_id": request.tenant_id,
                "request_id": request.request_id,
            },
        )

    async def _mark_applied(
        self,
        session: AsyncSession,
        request: GovernanceChangeRequest,
        *,
        at: datetime,
    ) -> GovernanceChangeRequest:
        applied = request.model_copy(
            update={"status": GovernanceChangeStatus.APPLIED, "applied_at": at}
        )
        await self._save_request(session, applied)
        return applied

    async def _current_retention_policy(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        at: datetime,
        include_future: bool,
        for_update: bool = False,
    ) -> RetentionPolicy | None:
        if include_future:
            sql = (
                "SELECT document FROM governance_retention_policies "
                "WHERE tenant_id = :tenant_id ORDER BY revision DESC LIMIT 1"
            )
        else:
            sql = (
                "SELECT document FROM governance_retention_policies "
                "WHERE tenant_id = :tenant_id AND effective_at <= :at "
                "ORDER BY effective_at DESC, revision DESC LIMIT 1"
            )
        if for_update:
            sql += " FOR UPDATE"
        params: dict[str, object] = {"tenant_id": tenant_id}
        if not include_future:
            params["at"] = at
        row = (await session.execute(text(sql), params)).mappings().first()
        return RetentionPolicy.model_validate(row["document"]) if row is not None else None

    async def _hold(
        self,
        session: AsyncSession,
        tenant_id: str,
        hold_id: UUID,
        *,
        for_update: bool = False,
    ) -> LegalHold | None:
        sql = (
            "SELECT document FROM governance_legal_holds "
            "WHERE tenant_id = :tenant_id AND hold_id = :hold_id"
        )
        if for_update:
            sql += " FOR UPDATE"
        row = (
            (await session.execute(text(sql), {"tenant_id": tenant_id, "hold_id": hold_id}))
            .mappings()
            .first()
        )
        return LegalHold.model_validate(row["document"]) if row is not None else None

    async def _active_holds(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[LegalHold, ...]:
        sql = (
            "SELECT document FROM governance_legal_holds "
            "WHERE tenant_id = :tenant_id AND status = 'active' ORDER BY placed_at, hold_id"
        )
        if for_update:
            sql += " FOR UPDATE"
        rows = (await session.execute(text(sql), {"tenant_id": tenant_id})).mappings().all()
        return tuple(LegalHold.model_validate(row["document"]) for row in rows)

    async def _current_trust_root(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> GovernedTrustRoot | None:
        sql = (
            "SELECT document FROM governance_trust_roots "
            "WHERE tenant_id = :tenant_id AND retired_at IS NULL "
            "ORDER BY revision DESC LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE"
        row = (await session.execute(text(sql), {"tenant_id": tenant_id})).mappings().first()
        return GovernedTrustRoot.model_validate(row["document"]) if row is not None else None

    async def _insert_trust_root(
        self,
        session: AsyncSession,
        root: GovernedTrustRoot,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO governance_trust_roots (
                  tenant_id, revision, policy_id, policy_sha256, activated_at,
                  retired_at, change_request_id, document
                ) VALUES (
                  :tenant_id, :revision, :policy_id, :policy_sha256, :activated_at,
                  :retired_at, :change_request_id, CAST(:document AS JSONB)
                )
                """
            ),
            {
                "tenant_id": root.tenant_id,
                "revision": root.revision,
                "policy_id": root.policy.policy_id,
                "policy_sha256": root.policy_sha256,
                "activated_at": root.activated_at,
                "retired_at": root.retired_at,
                "change_request_id": root.change_request_id,
                "document": root.model_dump_json(),
            },
        )

    async def _trust_root_history(
        self,
        session: AsyncSession,
        tenant_id: str,
    ) -> TrustRootHistory:
        rows = (
            (
                await session.execute(
                    text(
                        """
                    SELECT document
                    FROM governance_trust_roots
                    WHERE tenant_id = :tenant_id
                    ORDER BY revision
                    """
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return TrustRootHistory(
            tenant_id=tenant_id,
            roots=tuple(GovernedTrustRoot.model_validate(row["document"]) for row in rows),
        )

    async def _tenant_profile(
        self,
        session: AsyncSession,
        tenant_id: str,
    ) -> TenantIsolationProfile | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT document FROM tenant_isolation_profiles WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return TenantIsolationProfile.model_validate(row["document"]) if row is not None else None

    async def _support_grant(
        self,
        session: AsyncSession,
        tenant_id: str,
        grant_id: UUID,
    ) -> SupportElevationGrant | None:
        row = (
            (
                await session.execute(
                    text(
                        """
                    SELECT document
                    FROM support_elevation_grants
                    WHERE tenant_id = :tenant_id AND grant_id = :grant_id
                    """
                    ),
                    {"tenant_id": tenant_id, "grant_id": grant_id},
                )
            )
            .mappings()
            .first()
        )
        return SupportElevationGrant.model_validate(row["document"]) if row is not None else None

    @staticmethod
    async def _append_audit(session: AsyncSession, event: GovernanceAuditEvent) -> None:
        await session.execute(
            text(
                """
                INSERT INTO governance_audit_events (
                  event_id, tenant_id, event_type, occurred_at, request_id, document
                ) VALUES (
                  :event_id, :tenant_id, :event_type, :occurred_at, :request_id,
                  CAST(:document AS JSONB)
                )
                """
            ),
            {
                "event_id": event.event_id,
                "tenant_id": event.tenant_id,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at,
                "request_id": event.request_id,
                "document": event.model_dump_json(),
            },
        )

    @staticmethod
    def _same_actor(left: ActorRef, right: ActorRef) -> bool:
        return (
            left.kind == right.kind
            and left.id == right.id
            and left.tenant_id == right.tenant_id
            and left.workload_identity == right.workload_identity
        )

    @staticmethod
    def legal_hold_release_digest(*, tenant_id: str, hold_id: UUID) -> str:
        return sha256_hex(
            {
                "schema": "prodkit.legal-hold-release-intent/v1",
                "tenant_id": tenant_id,
                "hold_id": str(hold_id),
            }
        )

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

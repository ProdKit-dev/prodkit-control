from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    CompatibilityPolicy,
    GovernanceApprovalDecision,
    GovernanceRisk,
    GovernanceTargetType,
    KeyRotationPlan,
    LegalHold,
    MigrationPath,
    RetentionCandidate,
    RetentionDecision,
    RetentionDisposition,
    RetentionPolicy,
    RetentionRule,
    TenantAccessContext,
    TenantAccessMode,
    TenantCapability,
    TrustRootPolicy,
    sha256_hex,
)
from prodkit_control_postgres import PostgresGovernanceStore
from prodkit_control_runtime import Ed25519CheckpointSigner, OfflineAssuranceVerifier


def _connection_values() -> tuple[str, int, str, str, str]:
    return (
        os.environ["PRODKIT_POSTGRES_HOST"],
        int(os.environ["PRODKIT_POSTGRES_PORT"]),
        os.environ["PRODKIT_POSTGRES_DATABASE"],
        os.environ["PRODKIT_POSTGRES_USER"],
        os.environ["PRODKIT_POSTGRES_PASSWORD"],
    )


def _actor(tenant: str, actor_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant)


def _context(tenant: str, actor_id: str, *caps: TenantCapability) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant,
        actor=_actor(tenant, actor_id),
        capabilities=caps,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


class _DeletionAdapter:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(
        self,
        *,
        context: TenantAccessContext,
        candidate: RetentionCandidate,
        decision: RetentionDecision,
    ) -> str:
        assert decision.disposition is RetentionDisposition.DELETE
        assert context.tenant_id == candidate.tenant_id == decision.tenant_id
        self.deleted.append(candidate.resource_id)
        return f"postgres-delete:{candidate.resource_id}"


async def _approve(
    store: PostgresGovernanceStore,
    context: TenantAccessContext,
    request_id: UUID,
) -> None:
    await store.approve_change(
        context=context,
        request_id=request_id,
        decision=GovernanceApprovalDecision.APPROVE,
        reason="independent approval",
    )


async def _exercise_store(sessions: async_sessionmaker[AsyncSession]) -> None:
    tenant = "governance-ci-a"
    operator = _context(
        tenant,
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.READ,
        TenantCapability.DELETE,
        TenantCapability.LEGAL_HOLD,
        TenantCapability.EXPORT,
        TenantCapability.WRITE,
    )
    approver = _context(tenant, "operator-b", TenantCapability.APPROVE)
    store = PostgresGovernanceStore(sessions)
    now = datetime.now(UTC)

    policy = RetentionPolicy(
        policy_id=uuid4(),
        tenant_id=tenant,
        revision=1,
        effective_at=now,
        rules=(RetentionRule(resource_type="artifact", retain_for_seconds=60),),
        created_at=now,
        created_by=operator.actor,
    )
    request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.RETENTION_POLICY,
        target_id=str(policy.policy_id),
        proposed_digest=sha256_hex(policy),
        risk=GovernanceRisk.HIGH,
        reason="activate retention",
        ticket_reference="GOV-1",
    )
    try:
        await store.approve_change(
            context=operator,
            request_id=request.request_id,
            decision=GovernanceApprovalDecision.APPROVE,
            reason="self approval",
        )
    except AuthorizationDeniedError:
        pass
    else:
        raise AssertionError("high-risk governance self-approval must fail")
    await _approve(store, approver, request.request_id)
    await store.apply_retention_policy(policy, context=operator, request_id=request.request_id)
    assert await store.current_retention_policy(context=operator, at=now) == policy

    candidate = RetentionCandidate(
        tenant_id=tenant,
        resource_type="artifact",
        resource_id="artifact-1",
        created_at=now - timedelta(minutes=5),
        content_sha256="a" * 64,
    )
    assert (
        await store.evaluate_retention(context=operator, candidates=(candidate,), at=now)
    )[0].disposition is RetentionDisposition.DELETE

    hold = LegalHold(
        hold_id=uuid4(),
        tenant_id=tenant,
        reason="litigation",
        case_reference="CASE-1",
        resource_ids=(candidate.resource_id,),
        placed_at=now,
        placed_by=operator.actor,
    )
    await store.place_legal_hold(context=operator, hold=hold)
    held = (await store.evaluate_retention(context=operator, candidates=(candidate,), at=now))[0]
    assert held.disposition is RetentionDisposition.RETAIN and held.legal_hold_ids == (hold.hold_id,)

    release = await store.propose_legal_hold_release(
        context=operator,
        hold_id=hold.hold_id,
        reason="matter closed",
        ticket_reference="CASE-1",
    )
    await _approve(store, approver, release.request_id)
    await store.release_legal_hold(
        context=operator, hold_id=hold.hold_id, request_id=release.request_id
    )
    adapter = _DeletionAdapter()
    executions = await store.execute_retention(
        context=operator,
        candidates=(candidate,),
        adapter=adapter,
        at=now + timedelta(seconds=1),
    )
    assert len(executions) == 1 and adapter.deleted == [candidate.resource_id]

    old_signer = Ed25519CheckpointSigner.generate(key_id="gov-key-1", signer_id="gov-signer")
    new_signer = Ed25519CheckpointSigner.generate(key_id="gov-key-2", signer_id="gov-signer")
    root1 = TrustRootPolicy(
        policy_id="governance-signing",
        revision="1",
        trusted_keys=(old_signer.trusted_key(valid_from=now - timedelta(days=2)),),
        allowed_signers=("gov-signer",),
    )
    root_request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
        target_id=root1.policy_id,
        proposed_digest=sha256_hex(root1),
        risk=GovernanceRisk.CRITICAL,
        reason="bootstrap trust root",
        ticket_reference="KEY-1",
    )
    await _approve(store, approver, root_request.request_id)
    await store.bootstrap_trust_root(
        context=operator,
        policy=root1,
        activated_at=now - timedelta(days=1),
        request_id=root_request.request_id,
    )
    old_checkpoint = old_signer.sign(
        run_id=uuid4(),
        tenant_id=tenant,
        created_at=now - timedelta(hours=6),
        sequence=1,
        final_event_hash="b" * 64,
        evidence_bundle_sha256="c" * 64,
    )
    root2 = TrustRootPolicy(
        policy_id="governance-signing",
        revision="2",
        trusted_keys=(new_signer.trusted_key(valid_from=now),),
        allowed_signers=("gov-signer",),
    )
    rotation_request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
        target_id=root2.policy_id,
        proposed_digest=sha256_hex(root2),
        expected_current_digest=sha256_hex(root1),
        risk=GovernanceRisk.CRITICAL,
        reason="rotate trust root",
        ticket_reference="KEY-2",
    )
    await _approve(store, approver, rotation_request.request_id)
    history = await store.rotate_trust_root(
        context=operator,
        plan=KeyRotationPlan(
            rotation_id=uuid4(),
            tenant_id=tenant,
            from_revision=1,
            to_revision=2,
            activate_at=now,
            overlap_until=now + timedelta(hours=1),
            change_request_id=rotation_request.request_id,
            emergency=True,
        ),
        policy=root2,
    )
    OfflineAssuranceVerifier.verify_checkpoint(
        old_checkpoint,
        trust_policy=history.policy_for(old_checkpoint.created_at, key_id=old_checkpoint.key_id),
    )
    new_checkpoint = new_signer.sign(
        run_id=uuid4(),
        tenant_id=tenant,
        created_at=now + timedelta(minutes=1),
        sequence=1,
        final_event_hash="d" * 64,
        evidence_bundle_sha256="e" * 64,
    )
    OfflineAssuranceVerifier.verify_checkpoint(
        new_checkpoint,
        trust_policy=history.policy_for(new_checkpoint.created_at, key_id=new_checkpoint.key_id),
    )

    compatibility = CompatibilityPolicy(
        current_schema_version=7,
        minimum_supported_schema_version=5,
        migration_paths=(
            MigrationPath(from_schema_version=5, to_schema_version=6, minimum_control_version="0.5.0"),
            MigrationPath(from_schema_version=6, to_schema_version=7, minimum_control_version="0.6.0"),
        ),
    )
    transfer = await store.create_transfer_manifest(
        context=operator,
        source_control_version="0.5.0",
        source_schema_version=6,
        archive_sha256="f" * 64,
        bundle_manifest_sha256="1" * 64,
        trust_root_revision=2,
    )
    receipt = await store.record_verified_import(
        context=operator,
        manifest=transfer,
        archive_sha256="f" * 64,
        compatibility=compatibility,
    )
    assert receipt.verified

    audit = await store.list_audit(context=operator)
    assert any(event.event_type.value == "retention_deletion_executed" for event in audit)
    assert any(event.event_type.value == "trust_root_activated" for event in audit)

    support_context = operator.model_copy(
        update={
            "mode": TenantAccessMode.SUPPORT,
            "actor": _actor("platform", "support"),
            "elevation_id": uuid4(),
            "reason": "support",
            "ticket_reference": "SUP-1",
        }
    )
    try:
        await store.propose_change(
            context=support_context,
            target_type=GovernanceTargetType.TENANT_CONFIGURATION,
            target_id="blocked",
            proposed_digest="2" * 64,
            risk=GovernanceRisk.LOW,
            reason="must fail",
            ticket_reference="SUP-1",
        )
    except AuthorizationDeniedError:
        pass
    else:
        raise AssertionError("support elevation must not mutate governance state")


async def _exercise_database_guards() -> None:
    host, port, database, user, password = _connection_values()
    connection = await asyncpg.connect(host=host, port=port, database=database, user=user, password=password)
    try:
        try:
            await connection.execute("DELETE FROM governance_audit_events")
        except asyncpg.RaiseError:
            pass
        else:
            raise AssertionError("governance audit evidence must be append-only")
        try:
            await connection.execute("UPDATE governance_retention_policies SET tenant_id = 'other'")
        except asyncpg.RaiseError:
            pass
        else:
            raise AssertionError("governance tenant ownership must be immutable")
    finally:
        await connection.close()


async def main() -> None:
    host, port, database, user, password = _connection_values()
    engine = create_async_engine(
        f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}", pool_pre_ping=True
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _exercise_store(sessions)
    finally:
        await engine.dispose()
    await _exercise_database_guards()


if __name__ == "__main__":
    asyncio.run(main())

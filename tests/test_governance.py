from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    CompatibilityPolicy,
    EvidenceTransferVerification,
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
from prodkit_control_runtime import Ed25519CheckpointSigner, InMemoryGovernanceStore


def _actor(tenant_id: str, actor_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)


def _context(
    tenant_id: str,
    actor_id: str,
    *capabilities: TenantCapability,
) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_actor(tenant_id, actor_id),
        capabilities=capabilities,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


class _DeletionAdapter:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    async def delete(
        self,
        *,
        context: TenantAccessContext,
        candidate: RetentionCandidate,
        decision: RetentionDecision,
    ) -> str:
        assert context.tenant_id == candidate.tenant_id == decision.tenant_id
        assert decision.disposition is RetentionDisposition.DELETE
        self.deleted.append((candidate.resource_type, candidate.resource_id))
        return f"deleted:{candidate.resource_type}:{candidate.resource_id}"


async def _approve(
    store: InMemoryGovernanceStore,
    *,
    context: TenantAccessContext,
    request_id: UUID,
) -> None:
    await store.approve_change(
        context=context,
        request_id=request_id,
        decision=GovernanceApprovalDecision.APPROVE,
        reason="independent governance approval",
    )


@pytest.mark.asyncio
async def test_high_risk_governance_change_requires_independent_approval() -> None:
    store = InMemoryGovernanceStore()
    proposer = _context(
        "tenant-a",
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.APPROVE,
    )
    request = await store.propose_change(
        context=proposer,
        target_type=GovernanceTargetType.TENANT_CONFIGURATION,
        target_id="production",
        proposed_digest="a" * 64,
        risk=GovernanceRisk.HIGH,
        reason="change production policy",
        ticket_reference="CHG-100",
    )
    with pytest.raises(AuthorizationDeniedError, match="independent approver"):
        await store.approve_change(
            context=proposer,
            request_id=request.request_id,
            decision=GovernanceApprovalDecision.APPROVE,
            reason="self approval must fail",
        )


@pytest.mark.asyncio
async def test_standalone_governance_fails_closed_for_support_contexts() -> None:
    store = InMemoryGovernanceStore()
    now = datetime.now(UTC)
    support = TenantAccessContext(
        tenant_id="tenant-a",
        actor=_actor("platform", "support-1"),
        mode=TenantAccessMode.SUPPORT,
        capabilities=(TenantCapability.READ,),
        elevation_id=uuid4(),
        reason="support investigation",
        ticket_reference="SUP-1",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(AuthorizationDeniedError, match="live grant revalidation"):
        await store.list_audit(context=support)


@pytest.mark.asyncio
async def test_retention_deletion_and_legal_hold_precedence_are_rechecked() -> None:
    store = InMemoryGovernanceStore()
    operator = _context(
        "tenant-a",
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.READ,
        TenantCapability.LEGAL_HOLD,
        TenantCapability.DELETE,
    )
    approver = _context("tenant-a", "operator-b", TenantCapability.APPROVE)
    now = datetime.now(UTC)
    policy = RetentionPolicy(
        policy_id=uuid4(),
        tenant_id="tenant-a",
        revision=1,
        effective_at=now,
        default_retain_for_seconds=None,
        rules=(
            RetentionRule(
                resource_type="artifact",
                retain_for_seconds=30 * 86_400,
                deletion_grace_seconds=86_400,
            ),
            RetentionRule(resource_type="audit", deletion_allowed=False),
        ),
        created_at=now,
        created_by=operator.actor,
    )
    request = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.RETENTION_POLICY,
        target_id=str(policy.policy_id),
        proposed_digest=sha256_hex(policy),
        risk=GovernanceRisk.HIGH,
        reason="activate governed retention",
        ticket_reference="CHG-101",
    )
    await _approve(store, context=approver, request_id=request.request_id)
    await store.apply_retention_policy(policy, context=operator, request_id=request.request_id)

    candidate = RetentionCandidate(
        tenant_id="tenant-a",
        resource_type="artifact",
        resource_id="bundle-1",
        created_at=now - timedelta(days=40),
        content_sha256="b" * 64,
    )
    decision = (await store.evaluate_retention(context=operator, candidates=(candidate,), at=now))[
        0
    ]
    assert decision.disposition is RetentionDisposition.DELETE

    hold = LegalHold(
        hold_id=uuid4(),
        tenant_id="tenant-a",
        reason="active litigation",
        case_reference="CASE-7",
        resource_types=("artifact",),
        placed_at=now,
        placed_by=operator.actor,
    )
    await store.place_legal_hold(context=operator, hold=hold)
    held = (await store.evaluate_retention(context=operator, candidates=(candidate,), at=now))[0]
    assert held.disposition is RetentionDisposition.RETAIN
    assert held.legal_hold_ids == (hold.hold_id,)

    release = await store.propose_legal_hold_release(
        context=operator,
        hold_id=hold.hold_id,
        reason="matter closed",
        ticket_reference="CASE-7",
    )
    await _approve(store, context=approver, request_id=release.request_id)
    await store.release_legal_hold(
        context=operator,
        hold_id=hold.hold_id,
        request_id=release.request_id,
    )

    adapter = _DeletionAdapter()
    records = await store.execute_retention(
        context=operator, candidates=(candidate,), adapter=adapter
    )
    assert len(records) == 1
    assert adapter.deleted == [("artifact", "bundle-1")]


@pytest.mark.asyncio
async def test_key_rotation_preserves_historical_checkpoint_verification() -> None:
    store = InMemoryGovernanceStore()
    operator = _context(
        "tenant-a",
        "operator-a",
        TenantCapability.CONFIGURE,
        TenantCapability.READ,
    )
    approver = _context("tenant-a", "operator-b", TenantCapability.APPROVE)
    now = datetime.now(UTC)
    old_signer = Ed25519CheckpointSigner.generate(key_id="key-1", signer_id="tenant-a-signer")
    new_signer = Ed25519CheckpointSigner.generate(key_id="key-2", signer_id="tenant-a-signer")
    policy_v1 = TrustRootPolicy(
        policy_id="tenant-a-signing",
        revision="1",
        trusted_keys=(old_signer.trusted_key(valid_from=now - timedelta(days=2)),),
        allowed_signers=("tenant-a-signer",),
    )
    bootstrap = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
        target_id=policy_v1.policy_id,
        proposed_digest=sha256_hex(policy_v1),
        risk=GovernanceRisk.CRITICAL,
        reason="bootstrap managed trust root",
        ticket_reference="KEY-1",
    )
    await _approve(store, context=approver, request_id=bootstrap.request_id)
    await store.bootstrap_trust_root(
        context=operator,
        policy=policy_v1,
        activated_at=now - timedelta(days=1),
        request_id=bootstrap.request_id,
    )
    old_checkpoint = old_signer.sign(
        run_id=uuid4(),
        tenant_id="tenant-a",
        created_at=now - timedelta(hours=12),
        sequence=1,
        final_event_hash="c" * 64,
        evidence_bundle_sha256="d" * 64,
    )

    policy_v2 = TrustRootPolicy(
        policy_id="tenant-a-signing",
        revision="2",
        trusted_keys=(new_signer.trusted_key(valid_from=now),),
        allowed_signers=("tenant-a-signer",),
    )
    rotation = await store.propose_change(
        context=operator,
        target_type=GovernanceTargetType.TRUST_ROOT_POLICY,
        target_id=policy_v2.policy_id,
        proposed_digest=sha256_hex(policy_v2),
        expected_current_digest=sha256_hex(policy_v1),
        risk=GovernanceRisk.CRITICAL,
        reason="scheduled signer rotation",
        ticket_reference="KEY-2",
    )
    await _approve(store, context=approver, request_id=rotation.request_id)
    plan = KeyRotationPlan(
        rotation_id=uuid4(),
        tenant_id="tenant-a",
        from_revision=1,
        to_revision=2,
        activate_at=now,
        overlap_until=now + timedelta(hours=1),
        change_request_id=rotation.request_id,
        emergency=True,
    )
    history = await store.rotate_trust_root(context=operator, plan=plan, policy=policy_v2)
    new_checkpoint = new_signer.sign(
        run_id=uuid4(),
        tenant_id="tenant-a",
        created_at=now + timedelta(minutes=1),
        sequence=1,
        final_event_hash="e" * 64,
        evidence_bundle_sha256="f" * 64,
    )
    store.verify_checkpoint_history(old_checkpoint, history=history)
    store.verify_checkpoint_history(new_checkpoint, history=history)


@pytest.mark.asyncio
async def test_evidence_transfer_import_enforces_digest_and_upgrade_window() -> None:
    store = InMemoryGovernanceStore()
    context = _context(
        "tenant-a",
        "operator-a",
        TenantCapability.EXPORT,
        TenantCapability.WRITE,
    )
    compatibility = CompatibilityPolicy(
        current_schema_version=7,
        minimum_supported_schema_version=5,
        migration_paths=(
            MigrationPath(
                from_schema_version=5,
                to_schema_version=6,
                minimum_control_version="0.5.0",
            ),
            MigrationPath(
                from_schema_version=6,
                to_schema_version=7,
                minimum_control_version="0.6.0",
            ),
        ),
    )
    manifest = await store.create_transfer_manifest(
        context=context,
        source_control_version="0.5.0",
        source_schema_version=5,
        archive_sha256="1" * 64,
        bundle_manifest_sha256="2" * 64,
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
        trust_anchor_sha256="4" * 64,
    )
    receipt = await store.record_verified_import(
        context=context,
        manifest=manifest,
        verification=verification,
        archive_sha256="1" * 64,
        compatibility=compatibility,
    )
    assert receipt.verified
    assert compatibility.path_from(5) == compatibility.migration_paths
    with pytest.raises(ValueError, match="digest"):
        await store.record_verified_import(
            context=context,
            manifest=manifest,
            verification=verification,
            archive_sha256="3" * 64,
            compatibility=compatibility,
        )


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
    compatibility = CompatibilityPolicy(
        current_schema_version=7, minimum_supported_schema_version=7, migration_paths=()
    )
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

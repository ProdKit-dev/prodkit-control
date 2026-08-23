from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    SupportElevationGrant,
    TenantAccessContext,
    TenantAccessMode,
    TenantCapability,
    TenantIsolationProfile,
    TenantLifecycleStatus,
    sha256_hex,
)
from prodkit_control_runtime import (
    InMemoryExecutionAttemptStore,
    InMemoryTenantControlStore,
    TenantCacheNamespace,
)


def _actor(tenant_id: str, actor_id: str = "user") -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)


def test_tenant_access_context_rejects_cross_tenant_member_context() -> None:
    with pytest.raises(ValidationError, match="cannot cross tenant boundaries"):
        TenantAccessContext(
            tenant_id="tenant-b",
            actor=_actor("tenant-a"),
            capabilities=(TenantCapability.READ,),
            issued_at=datetime.now(UTC),
        )


def test_support_elevation_requires_reason_ticket_capabilities_and_expiry() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SupportElevationGrant(
            grant_id=uuid4(),
            target_tenant_id="tenant-a",
            operator=_actor("platform", "support-1"),
            capabilities=(),
            reason="incident investigation",
            ticket_reference="SUP-42",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
    with pytest.raises(ValidationError):
        TenantAccessContext(
            tenant_id="tenant-a",
            actor=_actor("platform", "support-1"),
            mode=TenantAccessMode.SUPPORT,
            capabilities=(TenantCapability.READ,),
            issued_at=now,
        )


@pytest.mark.asyncio
async def test_support_elevation_is_opt_in_bounded_audited_and_revocable() -> None:
    store = InMemoryTenantControlStore()
    tenant_actor = _actor("tenant-a", "owner")
    operator = _actor("platform", "support-1")
    disabled = TenantIsolationProfile(
        tenant_id="tenant-a",
        storage_partition="tenant-a",
        cache_namespace="tenant-a",
        allow_support_access=False,
    )
    await store.put_profile(disabled, actor=tenant_actor)
    with pytest.raises(AuthorizationDeniedError):
        await store.issue_support_grant(
            target_tenant_id="tenant-a",
            operator=operator,
            capabilities=(TenantCapability.READ,),
            reason="incident investigation",
            ticket_reference="SUP-42",
        )

    enabled = disabled.model_copy(update={"allow_support_access": True})
    await store.put_profile(enabled, actor=tenant_actor)
    grant = await store.issue_support_grant(
        target_tenant_id="tenant-a",
        operator=operator,
        capabilities=(TenantCapability.READ, TenantCapability.EXPORT),
        reason="incident investigation",
        ticket_reference="SUP-42",
        ttl_seconds=60,
    )
    context = await store.redeem_support_grant(
        target_tenant_id="tenant-a", grant_id=grant.grant_id, operator=operator
    )
    assert context.mode is TenantAccessMode.SUPPORT
    assert context.tenant_id == "tenant-a"
    context.require(TenantCapability.READ)
    with pytest.raises(PermissionError):
        context.require(TenantCapability.DELETE)

    await store.revoke_support_grant(
        target_tenant_id="tenant-a",
        grant_id=grant.grant_id,
        actor=operator,
        reason="work complete",
    )
    with pytest.raises(AuthorizationDeniedError):
        await store.redeem_support_grant(
            target_tenant_id="tenant-a", grant_id=grant.grant_id, operator=operator
        )
    audit = await store.list_audit("tenant-a")
    assert [event.event_type.value for event in audit][-3:] == [
        "support_elevation_issued",
        "support_elevation_used",
        "support_elevation_revoked",
    ]
    assert await store.list_audit("tenant-b") == ()


@pytest.mark.asyncio
async def test_legal_hold_blocks_deletion_and_export_is_tenant_scoped() -> None:
    store = InMemoryTenantControlStore()
    actor = _actor("tenant-a", "owner")
    held = await store.set_legal_hold(
        tenant_id="tenant-a", actor=actor, enabled=True, reason="litigation"
    )
    assert held.legal_hold is True
    with pytest.raises(AuthorizationDeniedError):
        await store.schedule_deletion(
            tenant_id="tenant-a",
            actor=actor,
            not_before=datetime.now(UTC) + timedelta(minutes=1),
            reason="account closure",
        )
    await store.set_legal_hold(
        tenant_id="tenant-a", actor=actor, enabled=False, reason="matter closed"
    )
    scheduled = await store.schedule_deletion(
        tenant_id="tenant-a",
        actor=actor,
        not_before=datetime.now(UTC) + timedelta(milliseconds=10),
        reason="account closure",
    )
    assert scheduled.status is TenantLifecycleStatus.DELETION_SCHEDULED
    manifest = await store.export_manifest(
        tenant_id="tenant-a",
        actor=actor,
        record_counts={"runs": 3, "events": 9},
        content_digests=(sha256_hex("export"),),
    )
    assert manifest.tenant_id == "tenant-a"
    with pytest.raises(AuthorizationDeniedError):
        await store.export_manifest(
            tenant_id="tenant-b", actor=actor, record_counts={"runs": 0}
        )


@pytest.mark.asyncio
async def test_known_foreign_execution_attempt_ids_are_not_visible() -> None:
    store = InMemoryExecutionAttemptStore()
    now = datetime.now(UTC)
    attempt = ExecutionAttemptRecord(
        attempt_id=uuid4(),
        action_id=uuid4(),
        run_id=uuid4(),
        tenant_id="tenant-a",
        idempotency_key="attempt-key",
        action_digest="a" * 64,
        executor_name="executor",
        executor_version="1.0.0",
        executor_identity="spiffe://prodkit.test/executor",
        state=ExecutionAttemptState.CLAIMED,
        claimed_at=now,
    )
    await store.create(attempt)
    assert await store.get(tenant_id="tenant-a", attempt_id=attempt.attempt_id) == attempt
    assert await store.get(tenant_id="tenant-b", attempt_id=attempt.attempt_id) is None
    assert (
        await store.latest_for_action(tenant_id="tenant-b", action_id=attempt.action_id) is None
    )


def test_cache_namespace_is_tenant_partitioned_for_many_adversarial_keys() -> None:
    payloads = ["same", "../same", "a/b", "", "tenant-a:shared"]
    for value in payloads:
        if not value:
            with pytest.raises(ValueError):
                TenantCacheNamespace.key(tenant_id="tenant-a", namespace="runs", key=value)
            continue
        left = TenantCacheNamespace.key(
            tenant_id="tenant-a", namespace="runs", key=value
        )
        right = TenantCacheNamespace.key(
            tenant_id="tenant-b", namespace="runs", key=value
        )
        assert left != right
        assert "tenant-a" not in left and "tenant-b" not in right

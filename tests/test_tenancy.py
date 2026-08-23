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


def _actor(tenant_id: str, actor_id: str = "user", **attributes: str) -> ActorRef:
    return ActorRef(
        kind=ActorKind.HUMAN,
        id=actor_id,
        tenant_id=tenant_id,
        attributes=attributes,
    )


def _context(tenant_id: str, *capabilities: TenantCapability) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_actor(tenant_id),
        capabilities=capabilities,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )


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
    issuer = _actor("platform", "authority", **{"prodkit.support_authority": "true"})
    with pytest.raises(ValidationError):
        SupportElevationGrant(
            grant_id=uuid4(),
            target_tenant_id="tenant-a",
            operator=_actor("platform", "support-1"),
            issued_by=issuer,
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
    tenant_context = _context("tenant-a", TenantCapability.CONFIGURE, TenantCapability.READ)
    operator = _actor("platform", "support-1", **{"prodkit.support_operator": "true"})
    issuer = _actor("platform", "authority", **{"prodkit.support_authority": "true"})
    disabled = TenantIsolationProfile(
        tenant_id="tenant-a",
        storage_partition="tenant-a",
        cache_namespace="tenant-a",
        allow_support_access=False,
    )
    await store.put_profile(disabled, context=tenant_context)
    with pytest.raises(AuthorizationDeniedError):
        await store.issue_support_grant(
            target_tenant_id="tenant-a",
            operator=operator,
            issued_by=issuer,
            capabilities=(TenantCapability.READ,),
            reason="incident investigation",
            ticket_reference="SUP-42",
        )

    enabled = disabled.model_copy(update={"allow_support_access": True})
    await store.put_profile(enabled, context=tenant_context)
    grant = await store.issue_support_grant(
        target_tenant_id="tenant-a",
        operator=operator,
        issued_by=issuer,
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
    with pytest.raises(AuthorizationDeniedError, match="cannot change tenant isolation policy"):
        await store.put_profile(enabled, context=context)

    await store.revoke_support_grant(
        target_tenant_id="tenant-a",
        grant_id=grant.grant_id,
        actor=operator,
        reason="work complete",
    )
    with pytest.raises(AuthorizationDeniedError):
        await store.list_audit(context=context)
    audit = await store.list_audit(context=tenant_context)
    assert [event.event_type.value for event in audit][-3:] == [
        "support_elevation_issued",
        "support_elevation_used",
        "support_elevation_revoked",
    ]


@pytest.mark.asyncio
async def test_legal_hold_blocks_deletion_and_export_is_tenant_scoped() -> None:
    store = InMemoryTenantControlStore()
    context = _context(
        "tenant-a",
        TenantCapability.LEGAL_HOLD,
        TenantCapability.DELETE,
        TenantCapability.EXPORT,
        TenantCapability.READ,
    )
    held = await store.set_legal_hold(context=context, enabled=True, reason="litigation")
    assert held.legal_hold is True
    with pytest.raises(AuthorizationDeniedError):
        await store.schedule_deletion(
            context=context,
            not_before=datetime.now(UTC) + timedelta(minutes=1),
            reason="account closure",
        )
    await store.set_legal_hold(context=context, enabled=False, reason="matter closed")
    scheduled = await store.schedule_deletion(
        context=context,
        not_before=datetime.now(UTC) + timedelta(milliseconds=10),
        reason="account closure",
    )
    assert scheduled.status is TenantLifecycleStatus.DELETION_SCHEDULED
    manifest = await store.export_manifest(
        context=context,
        record_counts={"runs": 3, "events": 9},
        content_digests=(sha256_hex("export"),),
    )
    assert manifest.tenant_id == "tenant-a"


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
    assert await store.latest_for_action(tenant_id="tenant-b", action_id=attempt.action_id) is None


@pytest.mark.parametrize("payload", ["same", "../same", "a/b", "tenant-a:shared", "unicode-λ"])
def test_cache_namespace_is_tenant_partitioned_for_adversarial_keys(payload: str) -> None:
    left = TenantCacheNamespace.key(tenant_id="tenant-a", namespace="runs", key=payload)
    right = TenantCacheNamespace.key(tenant_id="tenant-b", namespace="runs", key=payload)
    assert left != right
    assert "tenant-a" not in left and "tenant-b" not in right


def test_cache_namespace_rejects_blank_components() -> None:
    with pytest.raises(ValueError):
        TenantCacheNamespace.key(tenant_id="tenant-a", namespace="runs", key="")

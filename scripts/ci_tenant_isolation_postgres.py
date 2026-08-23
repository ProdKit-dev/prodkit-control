from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    TenantAccessContext,
    TenantCapability,
    TenantIsolationProfile,
)
from prodkit_control_postgres import PostgresTenantControlStore


def _connection_url() -> str:
    return (
        f"postgresql+asyncpg://{os.environ['PRODKIT_POSTGRES_USER']}:"
        f"{os.environ['PRODKIT_POSTGRES_PASSWORD']}@{os.environ['PRODKIT_POSTGRES_HOST']}:"
        f"{os.environ['PRODKIT_POSTGRES_PORT']}/{os.environ['PRODKIT_POSTGRES_DATABASE']}"
    )


def _tenant_actor(tenant_id: str, actor_id: str = "owner") -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)


def _tenant_context(tenant_id: str, *capabilities: TenantCapability) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_tenant_actor(tenant_id),
        capabilities=capabilities,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )


async def _expect_denied(awaitable) -> None:
    try:
        await awaitable
    except AuthorizationDeniedError:
        return
    raise AssertionError("operation unexpectedly crossed an authorization boundary")


async def _exercise(sessions: async_sessionmaker[AsyncSession]) -> None:
    store = PostgresTenantControlStore(sessions)
    tenant_a = _tenant_context(
        "tenant-control-a",
        TenantCapability.READ,
        TenantCapability.CONFIGURE,
        TenantCapability.EXPORT,
        TenantCapability.DELETE,
        TenantCapability.LEGAL_HOLD,
    )
    tenant_b = _tenant_context(
        "tenant-control-b",
        TenantCapability.READ,
        TenantCapability.CONFIGURE,
        TenantCapability.EXPORT,
    )
    profile_a = TenantIsolationProfile(
        tenant_id=tenant_a.tenant_id,
        storage_partition="tenant-control-a",
        cache_namespace="tenant-control-a",
        policy_profile="strict-production",
        signing_profile="tenant-key-a",
        retention_profile="enterprise-7y",
        executor_profile="isolated-a",
        allow_support_access=True,
    )
    profile_b = TenantIsolationProfile(
        tenant_id=tenant_b.tenant_id,
        storage_partition="tenant-control-b",
        cache_namespace="tenant-control-b",
        allow_support_access=False,
    )
    await store.put_profile(profile_a, context=tenant_a)
    await store.put_profile(profile_b, context=tenant_b)
    assert await store.get_profile(context=tenant_a) == profile_a
    assert await store.get_profile(context=tenant_b) == profile_b

    issuer = ActorRef(
        kind=ActorKind.HUMAN,
        id="support-authority",
        tenant_id="platform",
        attributes={"prodkit.support_authority": "true"},
    )
    operator = ActorRef(
        kind=ActorKind.HUMAN,
        id="support-operator",
        tenant_id="platform",
        workload_identity="spiffe://prodkit.test/support/operator",
        attributes={"prodkit.support_operator": "true"},
    )
    await _expect_denied(
        store.issue_support_grant(
            target_tenant_id=tenant_b.tenant_id,
            operator=operator,
            issued_by=issuer,
            capabilities=(TenantCapability.READ,),
            reason="diagnose tenant-b",
            ticket_reference="SUP-B",
            ttl_seconds=60,
        )
    )
    grant = await store.issue_support_grant(
        target_tenant_id=tenant_a.tenant_id,
        operator=operator,
        issued_by=issuer,
        capabilities=(TenantCapability.READ, TenantCapability.EXPORT),
        reason="diagnose tenant-a",
        ticket_reference="SUP-A",
        ttl_seconds=60,
    )
    support = await store.redeem_support_grant(
        target_tenant_id=tenant_a.tenant_id,
        grant_id=grant.grant_id,
        operator=operator,
    )
    assert await store.get_profile(context=support) == profile_a
    await _expect_denied(store.put_profile(profile_a, context=support))
    exported = await store.export_manifest(
        context=support,
        record_counts={"runs": 1, "events": 2},
        content_digests=("a" * 64,),
    )
    assert exported.tenant_id == tenant_a.tenant_id
    assert exported.elevation_id == grant.grant_id

    held = await store.set_legal_hold(context=tenant_a, enabled=True, reason="legal matter")
    assert held.legal_hold
    await _expect_denied(
        store.schedule_deletion(
            context=tenant_a,
            not_before=datetime.now(UTC) + timedelta(seconds=1),
            reason="closure",
        )
    )
    await store.set_legal_hold(context=tenant_a, enabled=False, reason="matter resolved")
    scheduled = await store.schedule_deletion(
        context=tenant_a,
        not_before=datetime.now(UTC) + timedelta(milliseconds=100),
        reason="closure",
    )
    assert scheduled.deletion_not_before is not None
    await asyncio.sleep(0.15)
    deleted = await store.complete_deletion(context=tenant_a)
    assert deleted.status.value == "deleted"
    assert await store.get_lifecycle(context=tenant_a) == deleted

    audit_a = await store.list_audit(context=tenant_a)
    audit_b = await store.list_audit(context=tenant_b)
    assert audit_a and audit_b
    assert all(event.tenant_id == tenant_a.tenant_id for event in audit_a)
    assert all(event.tenant_id == tenant_b.tenant_id for event in audit_b)
    assert any(event.elevation_id == grant.grant_id for event in audit_a)

    await store.revoke_support_grant(
        target_tenant_id=tenant_a.tenant_id,
        grant_id=grant.grant_id,
        actor=operator,
        reason="support work complete",
    )
    await _expect_denied(store.list_audit(context=support))

    async with sessions() as session:
        try:
            await session.execute(
                text(
                    "UPDATE tenant_lifecycle SET tenant_id = :foreign "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_a.tenant_id, "foreign": "tenant-control-foreign"},
            )
        except Exception:
            await session.rollback()
        else:
            raise AssertionError("database allowed tenant lifecycle ownership reassignment")

    async with sessions() as session:
        audit_id = await session.scalar(
            text(
                "SELECT audit_id FROM tenant_audit_events "
                "WHERE tenant_id = :tenant_id ORDER BY occurred_at LIMIT 1"
            ),
            {"tenant_id": tenant_a.tenant_id},
        )
        assert audit_id is not None
        try:
            await session.execute(
                text("DELETE FROM tenant_audit_events WHERE audit_id = :audit_id"),
                {"audit_id": audit_id},
            )
        except Exception:
            await session.rollback()
        else:
            raise AssertionError("database allowed deletion of append-only tenant audit evidence")


async def main() -> None:
    engine = create_async_engine(_connection_url(), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _exercise(sessions)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

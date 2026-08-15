from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ActionSpec,
    ActionTarget,
    ActorKind,
    ActorRef,
    EffectClass,
    RiskClass,
)


@pytest.fixture
def tenant_id() -> str:
    return "tenant-test"


@pytest.fixture
def human(tenant_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id="human-1", tenant_id=tenant_id)


@pytest.fixture
def agent(tenant_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.AGENT, id="agent-1", tenant_id=tenant_id)


def make_action(
    *,
    run_id,
    tenant_id: str,
    effect_class: EffectClass = EffectClass.READ,
    risk_class: RiskClass = RiskClass.LOW,
    operation: str = "demo.preview",
    idempotency_key: str | None = None,
) -> ActionSpec:
    now = datetime.now(UTC)
    return ActionSpec(
        action_id=uuid4(),
        run_id=run_id,
        tenant_id=tenant_id,
        executor="dry-run",
        operation=operation,
        effect_class=effect_class,
        risk_class=risk_class,
        target=ActionTarget(
            system="demo",
            environment="test",
            resource_type="resource",
            resource_id="resource-1",
        ),
        arguments={"value": 1},
        idempotency_key=idempotency_key or str(uuid4()),
        proposed_at=now,
        expires_at=now + timedelta(minutes=10),
        expected_effect={"changed": False},
    )


@pytest.fixture
def run_id():
    return uuid4()

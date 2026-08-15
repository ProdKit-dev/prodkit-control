from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ApprovalDecision,
    ApprovalOutcome,
    EffectClass,
    PolicyOutcome,
    RiskClass,
    sha256_hex,
)
from prodkit_control_runtime import DefaultPolicyEngine

from conftest import make_action


@pytest.mark.asyncio
async def test_policy_decision_is_deterministic(run_id, tenant_id: str) -> None:
    action = make_action(run_id=run_id, tenant_id=tenant_id)
    policy = DefaultPolicyEngine()
    first = await policy.evaluate(action)
    second = await policy.evaluate(action)
    assert first.decision_id == second.decision_id
    assert first.outcome is PolicyOutcome.ALLOW


@pytest.mark.asyncio
async def test_write_requires_approval(run_id, tenant_id: str) -> None:
    action = make_action(
        run_id=run_id,
        tenant_id=tenant_id,
        effect_class=EffectClass.WRITE,
        risk_class=RiskClass.HIGH,
    )
    decision = await DefaultPolicyEngine().evaluate(action)
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_approval_is_bound_to_exact_action(run_id, tenant_id: str, human) -> None:
    action = make_action(run_id=run_id, tenant_id=tenant_id)
    now = datetime.now(UTC)
    policy_id = uuid4()
    approval = ApprovalDecision(
        approval_id=uuid4(),
        action_id=action.action_id,
        action_digest=action.digest,
        target_digest=sha256_hex(action.target),
        tenant_id=tenant_id,
        environment=action.target.environment,
        policy_decision_id=policy_id,
        policy_revision="policy-v1",
        approver=human,
        approver_role="production_approver",
        decided_at=now,
        outcome=ApprovalOutcome.APPROVED,
        expires_at=now + timedelta(minutes=5),
        reason="Reviewed exact action",
    )
    assert approval.authorizes(
        action_digest=action.digest,
        target_digest=sha256_hex(action.target),
        policy_decision_id=policy_id,
        policy_revision="policy-v1",
        tenant_id=tenant_id,
        environment="test",
        at=now + timedelta(seconds=1),
    )
    changed = action.model_copy(update={"arguments": {"value": 2}})
    assert not approval.authorizes(
        action_digest=changed.digest,
        target_digest=sha256_hex(action.target),
        policy_decision_id=policy_id,
        policy_revision="policy-v1",
        tenant_id=tenant_id,
        environment="test",
        at=now + timedelta(seconds=1),
    )

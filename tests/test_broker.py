from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequiredError,
    EffectClass,
    RiskClass,
    VerificationOutcome,
    sha256_hex,
)
from prodkit_control_runtime import (
    ActionBroker,
    DefaultPolicyEngine,
    DigestEffectVerifier,
    DryRunExecutor,
    ExecutorRegistry,
    InMemoryApprovalStore,
    InMemoryEventLedger,
    InMemoryIdempotencyStore,
    RunCoordinator,
)

from conftest import make_action


async def make_runtime(human):
    ledger = InMemoryEventLedger()
    approvals = InMemoryApprovalStore()
    executors = ExecutorRegistry()
    executors.register(DryRunExecutor())
    policy = DefaultPolicyEngine()
    broker = ActionBroker(
        ledger=ledger,
        policy=policy,
        approvals=approvals,
        idempotency=InMemoryIdempotencyStore(),
        executors=executors,
        verifier=DigestEffectVerifier(),
    )
    coordinator = RunCoordinator(ledger)
    run = await coordinator.start_run(
        tenant_id=human.tenant_id,
        initiated_by=human,
        environment="test",
        purpose="test",
    )
    return ledger, approvals, policy, broker, run


@pytest.mark.asyncio
async def test_low_risk_action_executes_and_is_idempotent(human, agent) -> None:
    ledger, _, _, broker, run = await make_runtime(human)
    action = make_action(run_id=run.run_id, tenant_id=human.tenant_id, idempotency_key="same")
    first = await broker.execute(action, actor=agent, trace_id=run.trace_id)
    second = await broker.execute(action, actor=agent, trace_id=run.trace_id)
    assert first.verification.outcome is VerificationOutcome.PASSED
    assert second.reused_idempotent_result
    assert second.result.execution_attempt_id == first.result.execution_attempt_id
    await ledger.verify_run(run.run_id)


@pytest.mark.asyncio
async def test_write_waits_for_exact_approval(human, agent) -> None:
    _, approvals, policy, broker, run = await make_runtime(human)
    action = make_action(
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        effect_class=EffectClass.WRITE,
        risk_class=RiskClass.HIGH,
    )
    with pytest.raises(ApprovalRequiredError):
        await broker.execute(action, actor=agent, trace_id=run.trace_id)
    policy_decision = await policy.evaluate(action)
    now = datetime.now(UTC)
    await approvals.record(
        ApprovalDecision(
            approval_id=uuid4(),
            action_id=action.action_id,
            action_digest=action.digest,
            target_digest=sha256_hex(action.target),
            tenant_id=human.tenant_id,
            environment=action.target.environment,
            policy_decision_id=policy_decision.decision_id,
            policy_revision=policy_decision.policy_revision,
            approver=human,
            approver_role="production_approver",
            decided_at=now,
            outcome=ApprovalOutcome.APPROVED,
            expires_at=now + timedelta(minutes=5),
            reason="approved for test",
        )
    )
    outcome = await broker.execute(action, actor=agent, trace_id=run.trace_id)
    assert outcome.result.succeeded

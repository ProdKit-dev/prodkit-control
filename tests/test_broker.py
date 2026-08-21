from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from prodkit_control_core import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequiredError,
    DuplicateActionError,
    EffectClass,
    EventType,
    ExecutionAttemptState,
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
    InMemoryExecutionAttemptStore,
    InMemoryIdempotencyStore,
    RunCoordinator,
)

from conftest import make_action


class FailingExecutor:
    name = "failing"
    version = "1.0.0"

    async def execute(self, action):
        raise RuntimeError("simulated executor transport failure")

    async def observe(self, action, result):  # pragma: no cover - execution cannot reach this
        raise AssertionError("observe must not run")


class AttemptFailingExecutor:
    name = "attempt-failing"
    version = "1.0.0"
    identity = "spiffe://prodkit.test/executor/attempt-failing"

    async def execute(self, action):  # pragma: no cover - broker must use execute_attempt
        raise AssertionError("durable broker must use execute_attempt")

    async def execute_attempt(self, action, *, attempt_id: UUID):
        assert attempt_id
        raise RuntimeError("simulated crash after durable attempt start")

    async def observe(self, action, result):  # pragma: no cover - execution cannot reach this
        raise AssertionError("observe must not run")


async def make_runtime(human, *, extra_executor=None, execution_attempts=None):
    ledger = InMemoryEventLedger()
    approvals = InMemoryApprovalStore()
    executors = ExecutorRegistry()
    executors.register(DryRunExecutor())
    if extra_executor is not None:
        executors.register(extra_executor)
    policy = DefaultPolicyEngine()
    broker = ActionBroker(
        ledger=ledger,
        policy=policy,
        approvals=approvals,
        idempotency=InMemoryIdempotencyStore(),
        executors=executors,
        verifier=DigestEffectVerifier(),
        execution_attempts=execution_attempts,
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
    events = await ledger.list_run_events(run.run_id)
    reused_evidence = events[-2:]
    assert [event.event_type for event in reused_evidence] == [
        EventType.STATE_OBSERVED,
        EventType.VERIFICATION_COMPLETED,
    ]
    assert all(event.payload["reused_idempotent_result"] is True for event in reused_evidence)
    await ledger.verify_run(run.run_id)


@pytest.mark.asyncio
async def test_executor_exception_records_uncertainty_and_retains_idempotency(human, agent) -> None:
    ledger, _, _, broker, run = await make_runtime(human, extra_executor=FailingExecutor())
    action = make_action(run_id=run.run_id, tenant_id=human.tenant_id).model_copy(
        update={"executor": "failing"}
    )
    with pytest.raises(RuntimeError, match="simulated executor transport failure"):
        await broker.execute(action, actor=agent, trace_id=run.trace_id)
    events = await ledger.list_run_events(run.run_id)
    assert events[-1].event_type is EventType.EXECUTION_UNCERTAIN
    assert events[-1].payload["idempotency_key_retained"] is True

    with pytest.raises(DuplicateActionError, match="already in progress"):
        await broker.execute(action, actor=agent, trace_id=run.trace_id)


@pytest.mark.asyncio
async def test_durable_attempt_is_uncertain_after_ambiguous_executor_failure(human, agent) -> None:
    attempts = InMemoryExecutionAttemptStore()
    ledger, _, _, broker, run = await make_runtime(
        human,
        extra_executor=AttemptFailingExecutor(),
        execution_attempts=attempts,
    )
    action = make_action(
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        idempotency_key="durable-uncertain",
    ).model_copy(update={"executor": "attempt-failing"})

    with pytest.raises(RuntimeError, match="simulated crash after durable attempt start"):
        await broker.execute(action, actor=agent, trace_id=run.trace_id)

    attempt = await attempts.latest_for_action(action.action_id)
    assert attempt is not None
    assert attempt.state is ExecutionAttemptState.UNCERTAIN
    assert attempt.started_at is not None
    assert attempt.finished_at is not None
    assert attempt.uncertainty_reason == "executor raised after execution was marked started"
    assert attempt.error_type == "RuntimeError"

    events = await ledger.list_run_events(run.run_id)
    uncertain = [event for event in events if event.event_type is EventType.EXECUTION_UNCERTAIN]
    assert len(uncertain) == 1
    assert uncertain[0].payload["execution_attempt_id"] == str(attempt.attempt_id)
    assert uncertain[0].payload["automatic_retry_permitted"] is False

    with pytest.raises(DuplicateActionError, match="already in progress or uncertain"):
        await broker.execute(action, actor=agent, trace_id=run.trace_id)
    assert (await attempts.latest_for_action(action.action_id)) == attempt


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

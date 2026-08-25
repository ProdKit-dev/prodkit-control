"""Programmatic ProdKit Control dry-run using public Python surfaces."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from prodkit_control_core import (
    ActionSpec,
    ActionTarget,
    ActorKind,
    ActorRef,
    EffectClass,
    RiskClass,
    RunStatus,
)
from prodkit_control_runtime import (
    ActionBroker,
    DefaultPolicyEngine,
    DigestEffectVerifier,
    DryRunExecutor,
    ExecutorRegistry,
    InMemoryApprovalStore,
    InMemoryArtifactStore,
    InMemoryEventLedger,
    InMemoryIdempotencyStore,
    RunCoordinator,
)


async def main() -> None:
    tenant_id = "example-tenant"
    human = ActorRef(kind=ActorKind.HUMAN, id="example-user", tenant_id=tenant_id)
    agent = ActorRef(kind=ActorKind.AGENT, id="example-agent", tenant_id=tenant_id)

    ledger = InMemoryEventLedger()
    coordinator = RunCoordinator(ledger)
    registry = ExecutorRegistry()
    registry.register(DryRunExecutor())

    with TemporaryDirectory(prefix="prodkit-control-example-") as temporary_directory:
        broker = ActionBroker(
            ledger=ledger,
            policy=DefaultPolicyEngine(),
            approvals=InMemoryApprovalStore(),
            idempotency=InMemoryIdempotencyStore(),
            executors=registry,
            verifier=DigestEffectVerifier(),
            artifact_store=InMemoryArtifactStore(Path(temporary_directory) / "content"),
        )

        run = await coordinator.start_run(
            tenant_id=tenant_id,
            initiated_by=human,
            environment="development",
            purpose="Demonstrate an exact, policy-controlled dry-run action",
            source_intent={"example": "preview README without mutating a repository"},
        )

        now = datetime.now(UTC)
        action = ActionSpec(
            action_id=uuid4(),
            run_id=run.run_id,
            tenant_id=tenant_id,
            executor="dry-run",
            operation="repository.preview_change",
            effect_class=EffectClass.READ,
            risk_class=RiskClass.LOW,
            target=ActionTarget(
                system="git",
                environment="development",
                resource_type="repository",
                resource_id="prodkit/example",
            ),
            arguments={"path": "README.md", "operation": "preview"},
            idempotency_key=f"example-{run.run_id}",
            proposed_at=now,
            expires_at=now + timedelta(minutes=5),
            expected_effect={"changed": False, "previewed": True},
        )

        outcome = await broker.execute(action, actor=agent, trace_id=run.trace_id)
        await coordinator.complete_run(
            run.run_id,
            actor=human,
            status=RunStatus.SUCCEEDED,
            summary={
                "executor": outcome.result.executor_identity,
                "verification": outcome.verification.outcome.value,
            },
        )

        print(f"action digest: {action.digest}")
        print(f"executor: {outcome.result.executor_identity}")
        print(f"succeeded: {outcome.result.succeeded}")
        print(f"verification: {outcome.verification.outcome.value}")
        print(f"observation digest: {outcome.observation.state_digest}")


if __name__ == "__main__":
    asyncio.run(main())

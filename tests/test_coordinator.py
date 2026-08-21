from __future__ import annotations

import pytest

from prodkit_control_core import AuthorizationDeniedError, RunStatus
from prodkit_control_runtime import InMemoryEventLedger, RunCoordinator


@pytest.mark.asyncio
async def test_completion_rejects_cross_tenant_actor_before_state_mutation(human) -> None:
    ledger = InMemoryEventLedger()
    coordinator = RunCoordinator(ledger)
    run = await coordinator.start_run(
        tenant_id=human.tenant_id,
        initiated_by=human,
        environment="test",
        purpose="tenant boundary",
    )
    foreign_actor = human.model_copy(update={"tenant_id": "another-tenant"})

    with pytest.raises(AuthorizationDeniedError):
        await coordinator.complete_run(run.run_id, actor=foreign_actor)

    current = await coordinator.require_run(run.run_id)
    assert current.status is RunStatus.RUNNING
    assert len(await ledger.list_run_events(run.run_id)) == 1


@pytest.mark.asyncio
async def test_completion_requires_terminal_status_and_is_single_use(human) -> None:
    ledger = InMemoryEventLedger()
    coordinator = RunCoordinator(ledger)
    run = await coordinator.start_run(
        tenant_id=human.tenant_id,
        initiated_by=human,
        environment="test",
        purpose="terminal transition",
    )

    with pytest.raises(ValueError, match="terminal status"):
        await coordinator.complete_run(
            run.run_id,
            actor=human,
            status=RunStatus.WAITING_FOR_APPROVAL,
        )
    current = await coordinator.require_run(run.run_id)
    assert current.status is RunStatus.RUNNING

    completed = await coordinator.complete_run(
        run.run_id,
        actor=human,
        status=RunStatus.SUCCEEDED,
    )
    assert completed.status is RunStatus.SUCCEEDED

    with pytest.raises(ValueError, match="already terminal"):
        await coordinator.complete_run(run.run_id, actor=human, status=RunStatus.FAILED)

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from prodkit_control_core import ActionSpec, sha256_hex


@dataclass(frozen=True)
class TemporalWorkflowReceipt:
    workflow_id: str
    run_id: str


@dataclass(frozen=True)
class TemporalWorkflowState:
    workflow_id: str
    run_id: str
    status: str
    state_sha256: str


class TemporalClient(Protocol):
    async def start_workflow(
        self,
        *,
        workflow: str,
        workflow_id: str,
        task_queue: str,
        input: Mapping[str, Any],
    ) -> str: ...

    async def describe_workflow(self, *, workflow_id: str, run_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class TemporalAdapterConfig:
    allowed_workflows: frozenset[str]
    task_queue: str
    workflow_id_prefix: str = "prodkit"


class TemporalWorkflowAdapter:
    """Start durable workflows with action-digest and idempotency identity binding."""

    def __init__(self, config: TemporalAdapterConfig, *, client: TemporalClient) -> None:
        if not config.allowed_workflows:
            raise ValueError("Temporal adapter requires an explicit workflow allowlist")
        if not config.task_queue or not config.workflow_id_prefix:
            raise ValueError("Temporal task queue and workflow id prefix must be non-empty")
        self._config = config
        self._client = client

    async def start_action(self, action: ActionSpec) -> TemporalWorkflowReceipt:
        workflow = action.arguments.get("workflow")
        if not isinstance(workflow, str) or workflow not in self._config.allowed_workflows:
            raise PermissionError("Temporal workflow is not allowlisted")
        workflow_id = (
            f"{self._config.workflow_id_prefix}-{action.tenant_id}-"
            f"{sha256_hex((str(action.action_id), action.idempotency_key))[:24]}"
        )
        run_id = await self._client.start_workflow(
            workflow=workflow,
            workflow_id=workflow_id,
            task_queue=self._config.task_queue,
            input={
                "action": action.model_dump(mode="json"),
                "action_digest": action.digest,
                "idempotency_key": action.idempotency_key,
            },
        )
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Temporal client did not return a run id")
        return TemporalWorkflowReceipt(workflow_id=workflow_id, run_id=run_id)

    async def observe(self, receipt: TemporalWorkflowReceipt) -> TemporalWorkflowState:
        raw = await self._client.describe_workflow(
            workflow_id=receipt.workflow_id,
            run_id=receipt.run_id,
        )
        status = raw.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("Temporal workflow description is missing status")
        return TemporalWorkflowState(
            workflow_id=receipt.workflow_id,
            run_id=receipt.run_id,
            status=status,
            state_sha256=sha256_hex(dict(raw)),
        )

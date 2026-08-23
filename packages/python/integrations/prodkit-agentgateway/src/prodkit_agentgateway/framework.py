from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from prodkit_control_core import ActionSpec, ActionTarget, EffectClass, RiskClass


@dataclass(frozen=True, slots=True)
class AgentToolBinding:
    """Trusted mapping from a framework tool/function name to a ProdKit action boundary."""

    tool_name: str
    executor: str
    operation: str
    effect_class: EffectClass
    risk_class: RiskClass
    target_system: str
    target_environment: str
    target_resource_type: str
    target_resource_id_argument: str

    def __post_init__(self) -> None:
        values = (
            self.tool_name,
            self.executor,
            self.operation,
            self.target_system,
            self.target_environment,
            self.target_resource_type,
            self.target_resource_id_argument,
        )
        if any(not value.strip() for value in values):
            raise ValueError("agent tool bindings cannot contain blank identifiers")


@dataclass(frozen=True, slots=True)
class AgentToolInvocation:
    framework: str
    session_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.framework, self.session_id, self.call_id, self.tool_name)
        ):
            raise ValueError("agent tool invocations require framework/session/call/tool ids")


class AgentFrameworkActionAdapter:
    """Dependency-free boundary for LangGraph, Semantic Kernel, AutoGen, or custom agents.

    Framework-specific packages only need to normalize their tool/function call into
    `AgentToolInvocation`. This adapter deliberately returns an ActionSpec proposal and has no
    execution API.
    """

    def __init__(self, bindings: tuple[AgentToolBinding, ...]) -> None:
        self._bindings = {binding.tool_name: binding for binding in bindings}
        if not self._bindings or len(self._bindings) != len(bindings):
            raise ValueError("agent framework adapter requires unique non-empty tool bindings")

    def propose(
        self,
        invocation: AgentToolInvocation,
        *,
        run_id: UUID,
        tenant_id: str,
        proposed_at: datetime,
        expires_at: datetime | None = None,
    ) -> ActionSpec:
        try:
            binding = self._bindings[invocation.tool_name]
        except KeyError as exc:
            raise ValueError(f"unbound agent tool: {invocation.tool_name}") from exc
        raw_resource_id = invocation.arguments.get(binding.target_resource_id_argument)
        if not isinstance(raw_resource_id, (str, int)) or isinstance(raw_resource_id, bool):
            raise ValueError("agent resource id must resolve to a string or integer")
        resource_id = str(raw_resource_id).strip()
        if not resource_id:
            raise ValueError("agent resource id cannot be blank")

        namespace = (
            f"prodkit:agent:{tenant_id}:{invocation.framework}:{invocation.session_id}:"
            f"{invocation.call_id}:{invocation.tool_name}"
        )
        return ActionSpec(
            action_id=uuid5(NAMESPACE_URL, namespace),
            run_id=run_id,
            tenant_id=tenant_id,
            executor=binding.executor,
            operation=binding.operation,
            effect_class=binding.effect_class,
            risk_class=binding.risk_class,
            target=ActionTarget(
                system=binding.target_system,
                environment=binding.target_environment,
                resource_type=binding.target_resource_type,
                resource_id=resource_id,
            ),
            arguments=invocation.arguments,
            idempotency_key=namespace,
            proposed_at=proposed_at,
            expires_at=expires_at,
            policy_context={
                "interop.protocol": "agent-framework",
                "interop.framework": invocation.framework,
                "interop.session_id": invocation.session_id,
                "interop.call_id": invocation.call_id,
                "interop.tool_name": invocation.tool_name,
            },
        )

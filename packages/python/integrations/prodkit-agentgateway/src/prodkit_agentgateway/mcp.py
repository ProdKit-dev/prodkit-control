from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from prodkit_control_core import ActionSpec, ActionTarget, EffectClass, RiskClass

MCP_PROTOCOL_REVISION = "2025-11-25"
_RESERVED_CONTEXT_PREFIX = "interop."


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    """Administrator-owned mapping from an MCP tool name to a ProdKit action boundary."""

    tool_name: str
    executor: str
    operation: str
    effect_class: EffectClass
    risk_class: RiskClass
    target_system: str
    target_environment: str
    target_resource_type: str
    target_resource_id: str | None = None
    target_resource_id_argument: str | None = None
    target_region: str | None = None

    def __post_init__(self) -> None:
        required = {
            "tool_name": self.tool_name,
            "executor": self.executor,
            "operation": self.operation,
            "target_system": self.target_system,
            "target_environment": self.target_environment,
            "target_resource_type": self.target_resource_type,
        }
        blank = tuple(name for name, value in required.items() if not value.strip())
        if blank:
            raise ValueError(f"MCP tool binding contains blank fields: {', '.join(blank)}")
        if (self.target_resource_id is None) == (self.target_resource_id_argument is None):
            raise ValueError(
                "MCP tool binding requires exactly one static or argument-derived resource id"
            )


@dataclass(frozen=True, slots=True)
class MCPToolCall:
    server_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.server_id.strip() or not self.call_id.strip() or not self.tool_name.strip():
            raise ValueError("MCP tool calls require non-empty server, call, and tool ids")


class MCPActionAdapter:
    """Convert MCP tool calls into proposals; never execute model-controlled tool calls directly."""

    def __init__(
        self,
        bindings: tuple[MCPToolBinding, ...],
        *,
        protocol_revision: str = MCP_PROTOCOL_REVISION,
    ) -> None:
        if not protocol_revision.strip():
            raise ValueError("MCP protocol revision cannot be blank")
        by_name: dict[str, MCPToolBinding] = {}
        for binding in bindings:
            if binding.tool_name in by_name:
                raise ValueError(f"duplicate MCP tool binding: {binding.tool_name}")
            by_name[binding.tool_name] = binding
        if not by_name:
            raise ValueError("MCP action adapter requires at least one administrator-owned binding")
        self._bindings = by_name
        self._protocol_revision = protocol_revision

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def propose(
        self,
        call: MCPToolCall,
        *,
        run_id: UUID,
        tenant_id: str,
        proposed_at: datetime,
        expires_at: datetime | None = None,
        additional_policy_context: dict[str, str | int | float | bool | None] | None = None,
    ) -> ActionSpec:
        try:
            binding = self._bindings[call.tool_name]
        except KeyError as exc:
            raise ValueError(f"unbound MCP tool: {call.tool_name}") from exc

        resource_id = binding.target_resource_id
        if binding.target_resource_id_argument is not None:
            raw_resource_id = call.arguments.get(binding.target_resource_id_argument)
            if not isinstance(raw_resource_id, (str, int)) or isinstance(raw_resource_id, bool):
                raise ValueError("MCP resource-id argument must be a non-empty string or integer")
            resource_id = str(raw_resource_id).strip()
        if not resource_id:
            raise ValueError("MCP tool call resolved to an empty resource id")

        supplied_context = dict(additional_policy_context or {})
        reserved = tuple(
            sorted(key for key in supplied_context if key.startswith(_RESERVED_CONTEXT_PREFIX))
        )
        if reserved:
            raise ValueError(
                "additional MCP policy context cannot override reserved interoperability keys: "
                + ", ".join(reserved)
            )
        policy_context: dict[str, str | int | float | bool | None] = supplied_context
        policy_context.update(
            {
                "interop.protocol": "mcp",
                "interop.protocol_revision": self._protocol_revision,
                "interop.server_id": call.server_id,
                "interop.tool_name": call.tool_name,
                "interop.call_id": call.call_id,
            }
        )

        action_id = uuid5(
            NAMESPACE_URL,
            f"prodkit:mcp:{tenant_id}:{call.server_id}:{call.call_id}:{call.tool_name}",
        )
        return ActionSpec(
            action_id=action_id,
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
                region=binding.target_region,
            ),
            arguments=call.arguments,
            idempotency_key=(f"mcp:{tenant_id}:{call.server_id}:{call.call_id}:{call.tool_name}"),
            proposed_at=proposed_at,
            expires_at=expires_at,
            policy_context=policy_context,
        )

    @staticmethod
    def proposal_result(action: ActionSpec) -> dict[str, Any]:
        """Render an MCP CallToolResult-compatible object for a proposed, not executed, action."""

        structured = {
            "status": "proposed",
            "actionId": str(action.action_id),
            "actionDigest": action.digest,
            "runId": str(action.run_id),
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Action proposed to ProdKit Control; policy/approval/execution is pending."
                    ),
                }
            ],
            "structuredContent": structured,
            "isError": False,
        }

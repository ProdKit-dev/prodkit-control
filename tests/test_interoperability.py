from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from prodkit_agentgateway import MCPActionAdapter, MCPToolBinding, MCPToolCall
from prodkit_control_core import (
    ActionSpec,
    ActionTarget,
    EffectClass,
    IntegrityViolationError,
    PolicyDecision,
    PolicyOutcome,
    RiskClass,
)
from prodkit_control_runtime import ConjunctivePolicyEngine


def _action() -> ActionSpec:
    return ActionSpec(
        action_id=uuid4(),
        run_id=uuid4(),
        tenant_id="tenant-a",
        executor="filesystem",
        operation="write",
        effect_class=EffectClass.WRITE,
        risk_class=RiskClass.HIGH,
        target=ActionTarget(
            system="workspace",
            environment="production",
            resource_type="file",
            resource_id="README.md",
        ),
        idempotency_key="action-1",
        proposed_at=datetime.now(UTC),
    )


def test_mcp_tool_calls_become_deterministic_proposals_only() -> None:
    adapter = MCPActionAdapter(
        (
            MCPToolBinding(
                tool_name="prodkit.file.write",
                executor="filesystem",
                operation="write",
                effect_class=EffectClass.WRITE,
                risk_class=RiskClass.HIGH,
                target_system="workspace",
                target_environment="production",
                target_resource_type="file",
                target_resource_id_argument="path",
            ),
        )
    )
    run_id = uuid4()
    call = MCPToolCall(
        server_id="enterprise-mcp",
        call_id="call-42",
        tool_name="prodkit.file.write",
        arguments={"path": "README.md", "content": "proposal only"},
    )
    proposed_at = datetime.now(UTC)
    first = adapter.propose(
        call,
        run_id=run_id,
        tenant_id="tenant-a",
        proposed_at=proposed_at,
    )
    second = adapter.propose(
        call,
        run_id=run_id,
        tenant_id="tenant-a",
        proposed_at=proposed_at,
    )
    assert first == second
    assert first.effect_class is EffectClass.WRITE
    assert first.risk_class is RiskClass.HIGH
    assert first.target.resource_id == "README.md"
    assert first.policy_context["interop.protocol"] == "mcp"
    assert adapter.proposal_result(first)["structuredContent"]["status"] == "proposed"


def test_mcp_binding_rejects_untrusted_or_unbound_execution_shape() -> None:
    adapter = MCPActionAdapter(
        (
            MCPToolBinding(
                tool_name="prodkit.read",
                executor="http",
                operation="get",
                effect_class=EffectClass.READ,
                risk_class=RiskClass.LOW,
                target_system="inventory",
                target_environment="production",
                target_resource_type="record",
                target_resource_id="catalog",
            ),
        )
    )
    with pytest.raises(ValueError, match="unbound MCP tool"):
        adapter.propose(
            MCPToolCall(
                server_id="server",
                call_id="1",
                tool_name="unknown.destructive.tool",
                arguments={},
            ),
            run_id=uuid4(),
            tenant_id="tenant-a",
            proposed_at=datetime.now(UTC),
        )


class _StaticPolicy:
    def __init__(
        self,
        outcome: PolicyOutcome,
        *,
        engine: str,
        constraints: dict[str, str | int | float | bool | None] | None = None,
        action_id_override=None,
    ) -> None:
        self._outcome = outcome
        self._engine = engine
        self._constraints = constraints or {}
        self._action_id_override = action_id_override

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        now = datetime.now(UTC)
        return PolicyDecision(
            decision_id=uuid5(NAMESPACE_URL, f"{self._engine}:{action.digest}"),
            action_id=self._action_id_override or action.action_id,
            action_digest=action.digest,
            tenant_id=action.tenant_id,
            policy_engine=self._engine,
            policy_bundle="fixture",
            policy_revision="1",
            evaluated_at=now,
            outcome=self._outcome,
            reason_codes=(f"{self._engine}_reason",),
            constraints=self._constraints,
            required_approval_roles=("security",)
            if self._outcome is PolicyOutcome.REQUIRE_APPROVAL
            else (),
            expires_at=now + timedelta(minutes=10),
        )


@pytest.mark.asyncio
async def test_external_policy_cannot_weaken_stricter_prodkit_policy() -> None:
    action = _action()
    engine = ConjunctivePolicyEngine(
        (
            _StaticPolicy(PolicyOutcome.REQUIRE_APPROVAL, engine="prodkit"),
            _StaticPolicy(PolicyOutcome.ALLOW, engine="external"),
        )
    )
    decision = await engine.evaluate(action)
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.required_approval_roles == ("security",)


@pytest.mark.asyncio
async def test_policy_constraint_conflicts_and_cross_action_results_fail_closed() -> None:
    action = _action()
    conflicting = ConjunctivePolicyEngine(
        (
            _StaticPolicy(PolicyOutcome.ALLOW, engine="a", constraints={"region": "eu"}),
            _StaticPolicy(PolicyOutcome.ALLOW, engine="b", constraints={"region": "us"}),
        )
    )
    decision = await conflicting.evaluate(action)
    assert decision.outcome is PolicyOutcome.DENY
    assert "constraint_conflict:region" in decision.reason_codes

    mismatched = ConjunctivePolicyEngine(
        (
            _StaticPolicy(
                PolicyOutcome.ALLOW,
                engine="broken",
                action_id_override=uuid4(),
            ),
        )
    )
    with pytest.raises(IntegrityViolationError, match="another action"):
        await mismatched.evaluate(action)

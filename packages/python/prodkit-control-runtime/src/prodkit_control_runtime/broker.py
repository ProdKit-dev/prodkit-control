from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from prodkit_control_core import (
    ActionSpec,
    ActorRef,
    ApprovalDeniedError,
    ApprovalOutcome,
    ApprovalProvider,
    ApprovalRequiredError,
    ArtifactStore,
    AuthorizationDeniedError,
    ControlledExecutor,
    ControlEventDraft,
    DuplicateActionError,
    EffectVerifier,
    EventLedger,
    EventType,
    ExecutionResult,
    IdempotencyStore,
    IntegrityViolationError,
    PolicyEngine,
    PolicyOutcome,
    StateObservation,
    VerificationResult,
    sha256_hex,
)

from .executors import ExecutorRegistry
from .util import new_span_id


@dataclass(frozen=True)
class BrokerOutcome:
    action: ActionSpec
    result: ExecutionResult
    observation: StateObservation
    verification: VerificationResult
    reused_idempotent_result: bool = False


class ActionBroker:
    """Fail-closed lifecycle owner for externally visible actions."""

    def __init__(
        self,
        *,
        ledger: EventLedger,
        policy: PolicyEngine,
        approvals: ApprovalProvider,
        idempotency: IdempotencyStore,
        executors: ExecutorRegistry,
        verifier: EffectVerifier,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._approvals = approvals
        self._idempotency = idempotency
        self._executors = executors
        self._verifier = verifier
        self._artifact_store = artifact_store

    async def execute(
        self,
        action: ActionSpec,
        *,
        actor: ActorRef,
        trace_id: str,
    ) -> BrokerOutcome:
        if actor.tenant_id != action.tenant_id:
            raise AuthorizationDeniedError("actor tenant does not match action tenant")
        if action.expires_at is not None and datetime.now(UTC) >= action.expires_at:
            raise AuthorizationDeniedError("action has expired")

        await self._event(
            action,
            actor,
            trace_id,
            EventType.ACTION_PROPOSED,
            {"action": action.model_dump(mode="json"), "action_digest": action.digest},
        )

        policy = await self._policy.evaluate(action)
        if policy.action_digest != action.digest:
            raise AuthorizationDeniedError("policy decision is not bound to the action digest")
        await self._event(
            action,
            actor,
            trace_id,
            EventType.POLICY_EVALUATED,
            {"decision": policy.model_dump(mode="json")},
        )
        if policy.outcome is PolicyOutcome.DENY:
            raise AuthorizationDeniedError(
                f"policy denied action: {', '.join(policy.reason_codes) or 'no reason supplied'}"
            )

        target_digest = sha256_hex(action.target)
        if policy.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            approval = await self._approvals.find_valid_approval(
                action=action,
                policy_decision=policy,
                target_digest=target_digest,
            )
            if approval is None:
                await self._event(
                    action,
                    actor,
                    trace_id,
                    EventType.APPROVAL_REQUESTED,
                    {
                        "action_digest": action.digest,
                        "target_digest": target_digest,
                        "policy_decision_id": str(policy.decision_id),
                        "required_roles": list(policy.required_approval_roles),
                    },
                )
                raise ApprovalRequiredError(str(action.action_id), action.digest)
            await self._event(
                action,
                approval.approver,
                trace_id,
                EventType.APPROVAL_DECIDED,
                {"approval": approval.model_dump(mode="json")},
            )
            if approval.outcome is not ApprovalOutcome.APPROVED:
                raise ApprovalDeniedError(f"approval {approval.approval_id} is not approved")

        executor: ControlledExecutor = self._executors.get(action.executor)
        claimed = await self._idempotency.claim(
            tenant_id=action.tenant_id,
            key=action.idempotency_key,
            action_digest=action.digest,
        )
        if not claimed:
            existing = await self._idempotency.result(
                tenant_id=action.tenant_id,
                key=action.idempotency_key,
            )
            if existing is None:
                raise DuplicateActionError(
                    "an identical action is already in progress; execution is not duplicated"
                )
            self._validate_result(action, executor, existing, require_current_version=False)
            observation, verification = await self._observe_and_verify(
                action,
                actor=actor,
                trace_id=trace_id,
                executor=executor,
                result=existing,
                reused=True,
            )
            return BrokerOutcome(
                action=action,
                result=existing,
                observation=observation,
                verification=verification,
                reused_idempotent_result=True,
            )

        await self._event(
            action,
            actor,
            trace_id,
            EventType.EXECUTION_STARTED,
            {
                "executor": executor.name,
                "executor_version": executor.version,
                "action_digest": action.digest,
            },
        )
        try:
            result = await executor.execute(action)
        except Exception as exc:
            await self._event(
                action,
                actor,
                trace_id,
                EventType.EXECUTION_UNCERTAIN,
                {
                    "executor": executor.name,
                    "executor_version": executor.version,
                    "action_digest": action.digest,
                    "error_type": type(exc).__name__,
                    "idempotency_key_retained": True,
                },
            )
            raise

        self._validate_result(action, executor, result, require_current_version=True)
        await self._idempotency.complete(
            tenant_id=action.tenant_id,
            key=action.idempotency_key,
            result=result,
        )
        await self._event(
            action,
            actor,
            trace_id,
            EventType.EXECUTION_COMPLETED,
            {"result": result.model_dump(mode="json")},
        )

        observation, verification = await self._observe_and_verify(
            action,
            actor=actor,
            trace_id=trace_id,
            executor=executor,
            result=result,
            reused=False,
        )
        return BrokerOutcome(
            action=action,
            result=result,
            observation=observation,
            verification=verification,
        )

    async def _observe_and_verify(
        self,
        action: ActionSpec,
        *,
        actor: ActorRef,
        trace_id: str,
        executor: ControlledExecutor,
        result: ExecutionResult,
        reused: bool,
    ) -> tuple[StateObservation, VerificationResult]:
        observation = await executor.observe(action, result)
        if observation.action_id != action.action_id:
            raise IntegrityViolationError("state observation is not bound to the requested action")
        await self._event(
            action,
            actor,
            trace_id,
            EventType.STATE_OBSERVED,
            {
                "observation": observation.model_dump(mode="json"),
                "reused_idempotent_result": reused,
            },
        )

        verification = await self._verifier.verify(action, result, observation)
        if verification.action_id != action.action_id:
            raise IntegrityViolationError("verification is not bound to the requested action")
        await self._event(
            action,
            actor,
            trace_id,
            EventType.VERIFICATION_COMPLETED,
            {
                "verification": verification.model_dump(mode="json"),
                "reused_idempotent_result": reused,
            },
        )
        return observation, verification

    @staticmethod
    def _validate_result(
        action: ActionSpec,
        executor: ControlledExecutor,
        result: ExecutionResult,
        *,
        require_current_version: bool,
    ) -> None:
        if result.action_id != action.action_id:
            raise IntegrityViolationError("execution result is not bound to the requested action")
        if result.executor_name != executor.name:
            raise IntegrityViolationError("execution result does not match the selected executor")
        if require_current_version and result.executor_version != executor.version:
            raise IntegrityViolationError(
                "execution result does not match the selected executor version"
            )

    async def _event(
        self,
        action: ActionSpec,
        actor: ActorRef,
        trace_id: str,
        event_type: EventType,
        payload: dict[str, object],
    ) -> None:
        now = datetime.now(UTC)
        await self._ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=action.run_id,
                tenant_id=action.tenant_id,
                event_type=event_type,
                occurred_at=now,
                recorded_at=now,
                actor=actor,
                trace_id=trace_id,
                span_id=new_span_id(),
                action_id=action.action_id,
                payload=payload,
            )
        )

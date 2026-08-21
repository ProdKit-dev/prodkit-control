from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActionSpec,
    ActorRef,
    ApprovalDeniedError,
    ApprovalOutcome,
    ApprovalProvider,
    ApprovalRequiredError,
    ArtifactStore,
    AttemptAwareExecutor,
    AuthorizationDeniedError,
    ControlledExecutor,
    ControlEventDraft,
    CredentialLease,
    CredentialLeaseAwareExecutor,
    CredentialLeaseProvider,
    DuplicateActionError,
    EffectVerifier,
    EventLedger,
    EventType,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExecutionAttemptStore,
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
    """Fail-closed lifecycle owner for externally visible actions.

    A durable production profile supplies both ``execution_attempts`` and, for privileged
    executors, ``credential_leases``. The broker allocates the attempt identifier before the side
    effect and leaves both the attempt and idempotency claim in an explicit uncertain state after
    any ambiguous executor failure. It never silently retries an externally intended action.
    """

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
        execution_attempts: ExecutionAttemptStore | None = None,
        credential_leases: CredentialLeaseProvider | None = None,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._approvals = approvals
        self._idempotency = idempotency
        self._executors = executors
        self._verifier = verifier
        self._artifact_store = artifact_store
        self._execution_attempts = execution_attempts
        self._credential_leases = credential_leases

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
        durable_executor: AttemptAwareExecutor | None = None
        lease_executor: CredentialLeaseAwareExecutor | None = None
        if self._execution_attempts is not None or self._credential_leases is not None:
            if not isinstance(executor, AttemptAwareExecutor):
                raise AuthorizationDeniedError(
                    f"executor {executor.name!r} does not support durable attempt ownership"
                )
            durable_executor = executor
        if self._credential_leases is not None:
            if not isinstance(executor, CredentialLeaseAwareExecutor):
                raise AuthorizationDeniedError(
                    f"executor {executor.name!r} cannot consume an isolated credential lease"
                )
            lease_executor = executor

        lease: CredentialLease | None = None
        if self._credential_leases is not None and durable_executor is not None:
            lease = await self._credential_leases.issue(
                action=action,
                executor_identity=durable_executor.identity,
            )
            await self._event(
                action,
                actor,
                trace_id,
                EventType.CREDENTIAL_LEASE_ISSUED,
                {
                    "lease_id": str(lease.lease_id),
                    "executor_identity": lease.executor_identity,
                    "audience": lease.audience,
                    "scopes": list(lease.scopes),
                    "issued_at": lease.issued_at.isoformat(),
                    "expires_at": lease.expires_at.isoformat(),
                },
            )

        claimed = await self._idempotency.claim(
            tenant_id=action.tenant_id,
            key=action.idempotency_key,
            action_digest=action.digest,
        )
        if not claimed:
            if lease is not None:
                await self._revoke_lease(action, actor=actor, trace_id=trace_id, lease=lease)
            existing = await self._idempotency.result(
                tenant_id=action.tenant_id,
                key=action.idempotency_key,
            )
            if existing is None:
                raise DuplicateActionError(
                    "an identical action is already in progress or uncertain; execution is not duplicated"
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

        attempt_id: UUID | None = None
        attempt: ExecutionAttemptRecord | None = None
        if durable_executor is not None and self._execution_attempts is not None:
            attempt_id = uuid4()
            attempt = ExecutionAttemptRecord(
                attempt_id=attempt_id,
                action_id=action.action_id,
                run_id=action.run_id,
                tenant_id=action.tenant_id,
                idempotency_key=action.idempotency_key,
                action_digest=action.digest,
                executor_name=durable_executor.name,
                executor_version=durable_executor.version,
                executor_identity=durable_executor.identity,
                state=ExecutionAttemptState.CLAIMED,
                claimed_at=datetime.now(UTC),
            )
            await self._execution_attempts.create(attempt)
            attempt = attempt.model_copy(
                update={"state": ExecutionAttemptState.STARTED, "started_at": datetime.now(UTC)}
            )
            await self._execution_attempts.replace(attempt)

        await self._event(
            action,
            actor,
            trace_id,
            EventType.EXECUTION_STARTED,
            {
                "executor": executor.name,
                "executor_version": executor.version,
                "action_digest": action.digest,
                "execution_attempt_id": str(attempt_id) if attempt_id is not None else None,
                "credential_lease_id": str(lease.lease_id) if lease is not None else None,
            },
        )
        try:
            if lease_executor is not None and attempt_id is not None and lease is not None:
                result = await lease_executor.execute_attempt_with_lease(
                    action,
                    attempt_id=attempt_id,
                    credential_lease=lease,
                )
            elif durable_executor is not None and attempt_id is not None:
                result = await durable_executor.execute_attempt(action, attempt_id=attempt_id)
            else:
                result = await executor.execute(action)
        except Exception as exc:
            if attempt is not None and self._execution_attempts is not None:
                uncertain = attempt.model_copy(
                    update={
                        "state": ExecutionAttemptState.UNCERTAIN,
                        "finished_at": datetime.now(UTC),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "uncertainty_reason": "executor raised after execution was marked started",
                    }
                )
                await self._execution_attempts.replace(uncertain)
            revocation_error: Exception | None = None
            if lease is not None:
                try:
                    await self._revoke_lease(action, actor=actor, trace_id=trace_id, lease=lease)
                except Exception as revoke_exc:  # preserve the original ambiguous executor failure
                    revocation_error = revoke_exc
            await self._event(
                action,
                actor,
                trace_id,
                EventType.EXECUTION_UNCERTAIN,
                {
                    "executor": executor.name,
                    "executor_version": executor.version,
                    "action_digest": action.digest,
                    "execution_attempt_id": str(attempt_id) if attempt_id is not None else None,
                    "error_type": type(exc).__name__,
                    "idempotency_key_retained": True,
                    "automatic_retry_permitted": False,
                    "credential_revocation_failed": revocation_error is not None,
                },
            )
            if revocation_error is not None:
                exc.add_note(f"credential lease revocation also failed: {revocation_error}")
            raise

        self._validate_result(action, executor, result, require_current_version=True)
        if attempt_id is not None and result.execution_attempt_id != attempt_id:
            raise IntegrityViolationError("executor changed the broker-owned execution attempt id")

        if lease is not None:
            try:
                await self._revoke_lease(action, actor=actor, trace_id=trace_id, lease=lease)
            except Exception as exc:
                if attempt is not None and self._execution_attempts is not None:
                    uncertain = attempt.model_copy(
                        update={
                            "state": ExecutionAttemptState.UNCERTAIN,
                            "finished_at": datetime.now(UTC),
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "uncertainty_reason": (
                                "executor returned but credential lease revocation failed"
                            ),
                        }
                    )
                    await self._execution_attempts.replace(uncertain)
                await self._event(
                    action,
                    actor,
                    trace_id,
                    EventType.EXECUTION_UNCERTAIN,
                    {
                        "executor": executor.name,
                        "execution_attempt_id": (
                            str(attempt_id) if attempt_id is not None else None
                        ),
                        "reason": "credential_lease_revocation_failed",
                        "idempotency_key_retained": True,
                        "automatic_retry_permitted": False,
                    },
                )
                raise IntegrityViolationError(
                    "credential lease revocation failed after external execution"
                ) from exc

        if attempt is not None and self._execution_attempts is not None:
            terminal_state = (
                ExecutionAttemptState.SUCCEEDED
                if result.succeeded
                else ExecutionAttemptState.FAILED
            )
            terminal = attempt.model_copy(
                update={
                    "state": terminal_state,
                    "finished_at": result.completed_at,
                    "result_digest": sha256_hex(result),
                    "provider_operation_id": result.provider_operation_id,
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                }
            )
            await self._execution_attempts.replace(terminal)

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

    async def _revoke_lease(
        self,
        action: ActionSpec,
        *,
        actor: ActorRef,
        trace_id: str,
        lease: CredentialLease,
    ) -> None:
        if self._credential_leases is None:
            return
        try:
            await self._credential_leases.revoke(lease.lease_id)
        except Exception as exc:
            await self._event(
                action,
                actor,
                trace_id,
                EventType.CREDENTIAL_LEASE_REVOCATION_FAILED,
                {"lease_id": str(lease.lease_id), "error_type": type(exc).__name__},
            )
            raise
        await self._event(
            action,
            actor,
            trace_id,
            EventType.CREDENTIAL_LEASE_REVOKED,
            {"lease_id": str(lease.lease_id)},
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

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from prodkit_control_core.contracts.reconciliation import (
    ExpectedExternalAction,
    ExternalAuditEvent,
    ExternalStateObservation,
    ProductionCompletenessAssessment,
    ProductionCompletenessProfile,
    ReconciliationBatch,
    ReconciliationSeverity,
    ReconciliationSourceHealth,
)
from prodkit_control_core.contracts.verification import (
    ReconciliationFinding,
    ReconciliationOutcome,
)

_SEVERITY_RANK = {
    ReconciliationSeverity.INFO.value: 0,
    ReconciliationSeverity.LOW.value: 1,
    ReconciliationSeverity.MEDIUM.value: 2,
    ReconciliationSeverity.HIGH.value: 3,
    ReconciliationSeverity.CRITICAL.value: 4,
}


def _finding_id(
    *,
    run_id: UUID,
    source_system: str,
    outcome: ReconciliationOutcome,
    action_id: UUID | None,
    external_reference: str | None,
    discriminator: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        "|".join(
            (
                "prodkit-control-reconciliation-v1",
                str(run_id),
                source_system,
                outcome.value,
                str(action_id or ""),
                external_reference or "",
                discriminator,
            )
        ),
    )


def _finding(
    *,
    run_id: UUID,
    source_system: str,
    observed_at: datetime,
    outcome: ReconciliationOutcome,
    severity: ReconciliationSeverity,
    summary: str,
    action_id: UUID | None = None,
    external_reference: str | None = None,
    discriminator: str = "",
    details: dict[str, object] | None = None,
) -> ReconciliationFinding:
    return ReconciliationFinding(
        finding_id=_finding_id(
            run_id=run_id,
            source_system=source_system,
            outcome=outcome,
            action_id=action_id,
            external_reference=external_reference,
            discriminator=discriminator,
        ),
        run_id=run_id,
        action_id=action_id,
        reconciler=f"prodkit:{source_system}",
        source_system=source_system,
        observed_at=observed_at,
        outcome=outcome,
        severity=severity.value,
        summary=summary,
        external_reference=external_reference,
        details=details or {},
    )


class ReconciliationEngine:
    """Deterministic fail-closed comparison of controlled intent and external evidence."""

    def reconcile(
        self,
        *,
        run_id: UUID,
        expected_actions: tuple[ExpectedExternalAction, ...],
        batch: ReconciliationBatch,
    ) -> tuple[ReconciliationFinding, ...]:
        expected = {
            action.action_id: action
            for action in expected_actions
            if action.tenant_id == batch.tenant_id and action.source_system == batch.source_system
        }
        observations: dict[UUID, list[ExternalStateObservation]] = defaultdict(list)
        findings: list[ReconciliationFinding] = []

        if batch.health is not ReconciliationSourceHealth.HEALTHY:
            severity = (
                ReconciliationSeverity.HIGH
                if batch.health
                in {
                    ReconciliationSourceHealth.UNAVAILABLE,
                    ReconciliationSourceHealth.CONFLICTING,
                }
                else ReconciliationSeverity.MEDIUM
            )
            findings.append(
                _finding(
                    run_id=run_id,
                    source_system=batch.source_system,
                    observed_at=batch.collected_at,
                    outcome=(
                        ReconciliationOutcome.CONFLICTING_EVIDENCE
                        if batch.health is ReconciliationSourceHealth.CONFLICTING
                        else ReconciliationOutcome.UNVERIFIABLE
                    ),
                    severity=severity,
                    summary=f"source evidence is {batch.health.value}; reconciliation cannot pass",
                    discriminator=f"source-health:{batch.health.value}",
                    details={"source_health": batch.health.value},
                )
            )

        for observation in batch.observations:
            if observation.action_id is None or observation.action_id not in expected:
                findings.append(
                    _finding(
                        run_id=run_id,
                        source_system=batch.source_system,
                        observed_at=observation.observed_at,
                        outcome=ReconciliationOutcome.UNEXPECTED_EXTERNAL_ACTION,
                        severity=ReconciliationSeverity.HIGH,
                        summary="external state has no corresponding controlled action",
                        action_id=observation.action_id,
                        external_reference=observation.external_reference,
                        discriminator=f"observation:{observation.observation_id}",
                        details={"observation_id": observation.observation_id},
                    )
                )
                continue
            observations[observation.action_id].append(observation)

        for event in batch.audit_events:
            if event.action_id is None or event.action_id not in expected:
                findings.append(self._unexpected_audit_event(run_id=run_id, event=event))

        for action_id, action in sorted(expected.items(), key=lambda item: str(item[0])):
            action_observations = observations.get(action_id, [])
            if not action_observations:
                findings.append(
                    _finding(
                        run_id=run_id,
                        source_system=batch.source_system,
                        observed_at=batch.collected_at,
                        outcome=(
                            ReconciliationOutcome.UNVERIFIABLE
                            if batch.health is not ReconciliationSourceHealth.HEALTHY
                            else ReconciliationOutcome.MISSING_EXTERNAL_EVIDENCE
                        ),
                        severity=ReconciliationSeverity.HIGH,
                        summary=(
                            "expected action cannot be verified because source evidence is unavailable"
                            if batch.health is not ReconciliationSourceHealth.HEALTHY
                            else "controlled action has no external evidence"
                        ),
                        action_id=action_id,
                        external_reference=action.external_reference,
                        discriminator="missing",
                    )
                )
                continue

            digests = {item.state_digest for item in action_observations}
            if len(digests) > 1:
                findings.append(
                    _finding(
                        run_id=run_id,
                        source_system=batch.source_system,
                        observed_at=max(item.observed_at for item in action_observations),
                        outcome=ReconciliationOutcome.CONFLICTING_EVIDENCE,
                        severity=ReconciliationSeverity.HIGH,
                        summary="external observations disagree about action state",
                        action_id=action_id,
                        external_reference=action.external_reference,
                        discriminator="conflicting-digests:" + ",".join(sorted(digests)),
                        details={"observed_digests": sorted(digests)},
                    )
                )
                continue

            observed = max(action_observations, key=lambda item: item.observed_at)
            if (
                action.expected_state_digest is not None
                and observed.state_digest != action.expected_state_digest
            ):
                findings.append(
                    _finding(
                        run_id=run_id,
                        source_system=batch.source_system,
                        observed_at=observed.observed_at,
                        outcome=ReconciliationOutcome.STATE_MISMATCH,
                        severity=ReconciliationSeverity.HIGH,
                        summary="external state digest differs from controlled expectation",
                        action_id=action_id,
                        external_reference=observed.external_reference or action.external_reference,
                        discriminator=f"mismatch:{action.expected_state_digest}:{observed.state_digest}",
                        details={
                            "expected_digest": action.expected_state_digest,
                            "observed_digest": observed.state_digest,
                        },
                    )
                )
                continue

            if batch.health is ReconciliationSourceHealth.HEALTHY:
                findings.append(
                    _finding(
                        run_id=run_id,
                        source_system=batch.source_system,
                        observed_at=observed.observed_at,
                        outcome=ReconciliationOutcome.MATCHED,
                        severity=ReconciliationSeverity.INFO,
                        summary="external evidence matches controlled action",
                        action_id=action_id,
                        external_reference=observed.external_reference or action.external_reference,
                        discriminator=f"matched:{observed.state_digest}",
                    )
                )

        return tuple(
            sorted(
                {finding.finding_id: finding for finding in findings}.values(),
                key=lambda item: (
                    -_SEVERITY_RANK.get(item.severity, 99),
                    item.outcome.value,
                    str(item.action_id or ""),
                    str(item.finding_id),
                ),
            )
        )

    @staticmethod
    def _unexpected_audit_event(
        *,
        run_id: UUID,
        event: ExternalAuditEvent,
    ) -> ReconciliationFinding:
        return _finding(
            run_id=run_id,
            source_system=event.source_system,
            observed_at=event.occurred_at,
            outcome=ReconciliationOutcome.UNEXPECTED_EXTERNAL_ACTION,
            severity=ReconciliationSeverity.HIGH,
            summary="external audit event has no corresponding controlled action",
            action_id=event.action_id,
            external_reference=event.external_reference,
            discriminator=f"audit:{event.event_id}:{event.payload_digest}",
            details={"event_id": event.event_id, "event_type": event.event_type},
        )


def assess_production_completeness(
    *,
    profile: ProductionCompletenessProfile,
    assessed_at: datetime,
    source_health: dict[str, tuple[ReconciliationSourceHealth, datetime | None]],
    findings: tuple[ReconciliationFinding, ...],
) -> ProductionCompletenessAssessment:
    healthy: list[str] = []
    stale: list[str] = []
    unavailable: list[str] = []
    conflicting: list[str] = []

    for source in profile.required_sources:
        health, high_watermark = source_health.get(
            source, (ReconciliationSourceHealth.UNAVAILABLE, None)
        )
        if (
            health is ReconciliationSourceHealth.HEALTHY
            and high_watermark is not None
            and (assessed_at - high_watermark).total_seconds() <= profile.max_source_age_seconds
        ):
            healthy.append(source)
        elif health is ReconciliationSourceHealth.CONFLICTING:
            conflicting.append(source)
        elif health is ReconciliationSourceHealth.UNAVAILABLE or high_watermark is None:
            unavailable.append(source)
        else:
            stale.append(source)

    blocking = tuple(
        finding.finding_id
        for finding in findings
        if finding.source_system in profile.required_sources
        and finding.outcome is not ReconciliationOutcome.MATCHED
    )
    complete = not stale and not unavailable and not conflicting and not blocking
    if profile.require_matched_reconciliation:
        matched_sources = {
            finding.source_system
            for finding in findings
            if finding.outcome is ReconciliationOutcome.MATCHED
        }
        complete = complete and set(profile.required_sources).issubset(matched_sources)

    return ProductionCompletenessAssessment(
        profile_id=profile.profile_id,
        tenant_id=profile.tenant_id,
        organization_id=profile.organization_id,
        assessed_at=assessed_at,
        complete=complete,
        healthy_sources=tuple(sorted(healthy)),
        stale_sources=tuple(sorted(stale)),
        unavailable_sources=tuple(sorted(unavailable)),
        conflicting_sources=tuple(sorted(conflicting)),
        blocking_findings=blocking,
    )

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr, Sha256


class VerificationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ReconciliationOutcome(StrEnum):
    MATCHED = "matched"
    MISSING_EXTERNAL_EVIDENCE = "missing_external_evidence"
    UNEXPECTED_EXTERNAL_ACTION = "unexpected_external_action"
    STATE_MISMATCH = "state_mismatch"
    UNVERIFIABLE = "unverifiable"


class StateObservation(ContractModel):
    observation_id: UUID
    action_id: UUID
    source: NonBlankStr
    observed_at: AwareDatetime
    state_digest: Sha256
    state: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()


class VerificationResult(ContractModel):
    verification_id: UUID
    action_id: UUID
    verifier: NonBlankStr
    verifier_version: NonBlankStr
    verified_at: AwareDatetime
    outcome: VerificationOutcome
    expected_digest: Sha256 | None = None
    observed_digest: Sha256 | None = None
    checks: tuple[NonBlankStr, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()


class ReconciliationFinding(ContractModel):
    finding_id: UUID
    run_id: UUID
    action_id: UUID | None = None
    reconciler: NonBlankStr
    source_system: NonBlankStr
    observed_at: AwareDatetime
    outcome: ReconciliationOutcome
    severity: NonBlankStr
    summary: NonBlankStr
    external_reference: NonBlankStr | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()

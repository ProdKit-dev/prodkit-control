from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, NonBlankStr


class WorkState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


class FencedLease(ContractModel):
    """Exclusive time-bounded ownership carrying a monotonically increasing fencing token."""

    schema_name: str = "prodkit.fenced-lease"
    schema_version: str = "1.0.0"
    lease_id: UUID
    tenant_id: NonBlankStr
    resource_key: NonBlankStr
    owner_id: NonBlankStr
    fence_token: int = Field(ge=1)
    acquired_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> FencedLease:
        if self.expires_at <= self.acquired_at:
            raise ValueError("fenced lease must expire after acquisition")
        return self

    def is_expired(self, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("lease expiry checks require an aware datetime")
        return at >= self.expires_at


class DurableWorkItem(ContractModel):
    """Provider-neutral durable scheduler item with explicit retry/dead-letter state."""

    schema_name: str = "prodkit.durable-work-item"
    schema_version: str = "1.0.0"
    job_id: UUID
    tenant_id: NonBlankStr
    queue: NonBlankStr
    kind: NonBlankStr
    idempotency_key: NonBlankStr
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: AwareDatetime
    available_at: AwareDatetime
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=1000)
    state: WorkState = WorkState.QUEUED
    completed_at: AwareDatetime | None = None
    last_error: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> DurableWorkItem:
        if self.available_at < self.created_at:
            raise ValueError("work cannot become available before it is created")
        if self.attempt > self.max_attempts:
            raise ValueError("work attempt cannot exceed max_attempts")
        if self.state is WorkState.SUCCEEDED:
            if self.completed_at is None:
                raise ValueError("succeeded work requires completed_at")
        elif self.completed_at is not None:
            raise ValueError("only succeeded work may have completed_at")
        if self.state is WorkState.DEAD_LETTER and not self.last_error:
            raise ValueError("dead-letter work requires last_error")
        return self

    @property
    def resource_key(self) -> str:
        return f"queue:{self.queue}:job:{self.job_id}"


class LeasedWorkItem(ContractModel):
    """Work item plus the exact fencing lease required for a state transition."""

    schema_name: str = "prodkit.leased-work-item"
    schema_version: str = "1.0.0"
    item: DurableWorkItem
    lease: FencedLease

    @model_validator(mode="after")
    def validate_binding(self) -> LeasedWorkItem:
        if self.item.state is not WorkState.LEASED:
            raise ValueError("leased work item must be in leased state")
        if self.item.tenant_id != self.lease.tenant_id:
            raise ValueError("work item and lease tenant differ")
        if self.item.resource_key != self.lease.resource_key:
            raise ValueError("work item and lease resource differ")
        return self


class QueueSnapshot(ContractModel):
    schema_name: str = "prodkit.queue-snapshot"
    schema_version: str = "1.0.0"
    queue: NonBlankStr
    tenant_id: NonBlankStr | None = None
    queued: int = Field(ge=0)
    leased: int = Field(ge=0)
    dead_letter: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    captured_at: AwareDatetime

    @property
    def active_depth(self) -> int:
        return self.queued + self.leased


class CapacityEnvelope(ContractModel):
    """Published operating envelope for a supported control-plane deployment profile."""

    schema_name: str = "prodkit.capacity-envelope"
    schema_version: str = "1.0.0"
    profile_id: NonBlankStr
    max_queue_depth: int = Field(ge=1)
    max_in_flight: int = Field(ge=1)
    max_per_tenant_in_flight: int = Field(ge=1)
    lease_ttl_seconds: float = Field(gt=0, le=86400)
    shutdown_grace_seconds: float = Field(gt=0, le=3600)
    qualification_concurrency: int = Field(ge=1)
    qualification_work_items: int = Field(ge=1)
    qualification_soak_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_limits(self) -> CapacityEnvelope:
        if self.max_per_tenant_in_flight > self.max_in_flight:
            raise ValueError("per-tenant in-flight limit cannot exceed global limit")
        if self.qualification_concurrency > self.max_in_flight:
            raise ValueError("qualification concurrency exceeds declared in-flight capacity")
        if self.qualification_work_items > self.max_queue_depth:
            raise ValueError("qualification work items exceed declared queue capacity")
        return self

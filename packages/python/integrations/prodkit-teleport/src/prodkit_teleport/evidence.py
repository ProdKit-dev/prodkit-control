from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from prodkit_control_core import SecurityAuditEvent, SecurityOutcome, SecuritySeverity


class TeleportEvidenceAdapter:
    """Normalize Teleport audit/session events into canonical security audit evidence."""

    _SENSITIVE_KEYS = {"token", "password", "secret", "private_key", "authorization", "credential"}

    def normalize(
        self,
        raw: Mapping[str, Any],
        *,
        tenant_id: str,
        action_id: UUID | None = None,
    ) -> SecurityAuditEvent:
        if not tenant_id:
            raise ValueError("Teleport evidence requires an explicit tenant_id")
        event_type = self._required_str(raw, "event")
        occurred_at = self._timestamp(raw.get("time") or raw.get("timestamp"))
        external_id = raw.get("uid") or raw.get("id") or raw.get("event_id")
        stable_id = (
            str(external_id)
            if external_id is not None
            else f"{event_type}:{occurred_at.isoformat()}:{raw.get('user', '')}"
        )
        try:
            event_id = UUID(stable_id)
        except ValueError:
            event_id = uuid5(NAMESPACE_URL, f"teleport:{stable_id}")
        success = raw.get("success")
        code = raw.get("code")
        if isinstance(success, bool):
            outcome = SecurityOutcome.ALLOWED if success else SecurityOutcome.FAILED
        elif isinstance(code, str) and code.lower() in {"success", "ok"}:
            outcome = SecurityOutcome.ALLOWED
        else:
            outcome = SecurityOutcome.DETECTED
        lowered = event_type.lower()
        severity = (
            SecuritySeverity.HIGH
            if any(marker in lowered for marker in ("deny", "failed", "lock", "root", "sudo"))
            else SecuritySeverity.INFO
        )
        principal = raw.get("user") or raw.get("user_name")
        request_id = raw.get("sid") or raw.get("session_id") or external_id
        attributes: dict[str, str] = {}
        for key in ("cluster_name", "namespace", "server_id", "resource", "login", "code"):
            value = raw.get(key)
            if value is not None:
                attributes[key] = str(value)
        for key, value in raw.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in self._SENSITIVE_KEYS or any(
                marker in normalized for marker in self._SENSITIVE_KEYS
            ):
                continue
            if key.startswith("prodkit_") and isinstance(value, (str, int, float, bool)):
                attributes[str(key)] = str(value)
        return SecurityAuditEvent(
            event_id=event_id,
            occurred_at=occurred_at,
            event_type=f"teleport.{event_type}",
            severity=severity,
            outcome=outcome,
            tenant_id=tenant_id,
            principal_id=str(principal) if principal is not None else None,
            action_id=action_id,
            request_id=str(request_id) if request_id is not None else None,
            attributes=attributes,
        )

    @staticmethod
    def _required_str(raw: Mapping[str, Any], key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Teleport event requires non-empty {key!r}")
        return value

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Teleport timestamp must be timezone-aware")
            return value.astimezone(UTC)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Teleport timestamp must include a timezone")
            return parsed.astimezone(UTC)
        raise ValueError("Teleport event requires an ISO-8601 timestamp")

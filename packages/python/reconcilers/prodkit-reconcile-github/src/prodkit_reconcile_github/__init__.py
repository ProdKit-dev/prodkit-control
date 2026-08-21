from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class GitHubReconciler(MappingReconciler):
    """Normalize GitHub workflow/deployment audit evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("github")

    def normalize_provider_records(
        self,
        *,
        tenant_id: str,
        records: list[dict[str, object]],
        collected_at: datetime,
        cursor: str | None = None,
    ) -> ReconciliationBatch:
        normalized: list[dict[str, object]] = []
        for record in records:
            kind = str(record.get("kind", "workflow_run"))
            record_id = str(record["id"])
            observed_at = record.get("updated_at", collected_at)
            if kind == "audit":
                normalized.append(
                    {
                        "record_type": "audit",
                        "event_id": record_id,
                        "occurred_at": observed_at,
                        "event_type": str(record.get("event_type", "github.audit")),
                        "actor": record.get("actor"),
                        "resource": record.get("resource"),
                        "action_id": record.get("action_id"),
                        "external_reference": record.get("html_url"),
                        "payload": dict(record),
                    }
                )
                continue
            normalized.append(
                {
                    "record_type": "state",
                    "observation_id": f"workflow:{record_id}",
                    "observed_at": observed_at,
                    "action_id": record.get("action_id"),
                    "external_reference": record.get("html_url"),
                    "state": {
                        "head_sha": record.get("head_sha"),
                        "status": record.get("status"),
                        "conclusion": record.get("conclusion"),
                        "workflow": record.get("workflow"),
                    },
                }
            )
        return self.normalize(
            tenant_id=tenant_id,
            records=normalized,
            collected_at=collected_at,
            cursor=cursor,
        )


__all__ = ("GitHubReconciler",)

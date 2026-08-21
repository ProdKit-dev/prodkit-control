from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class CIReconciler(MappingReconciler):
    """Normalize CI/build provider records for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("ci")

    def normalize_provider_records(
        self,
        *,
        tenant_id: str,
        records: list[dict[str, object]],
        collected_at: datetime,
        cursor: str | None = None,
    ) -> ReconciliationBatch:
        normalized = [
            {
                "record_type": "state",
                "observation_id": f"build:{record['build_id']}",
                "observed_at": record.get("finished_at", collected_at),
                "action_id": record.get("action_id"),
                "external_reference": record.get("url"),
                "state": {
                    "build_id": record["build_id"],
                    "revision": record.get("revision"),
                    "status": record.get("status"),
                    "artifact_digest": record.get("artifact_digest"),
                },
            }
            for record in records
        ]
        return self.normalize(
            tenant_id=tenant_id,
            records=normalized,
            collected_at=collected_at,
            cursor=cursor,
        )


__all__ = ("CIReconciler",)

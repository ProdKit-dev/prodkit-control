from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class DatabaseReconciler(MappingReconciler):
    """Normalize database/control-plane migration records for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("database")

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
                "observation_id": f"migration:{record['migration_id']}",
                "observed_at": record.get("applied_at", collected_at),
                "action_id": record.get("action_id"),
                "external_reference": record.get("external_reference"),
                "state": {
                    "migration_id": record["migration_id"],
                    "schema_version": record.get("schema_version"),
                    "database": record.get("database"),
                    "status": record.get("status", "applied"),
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


__all__ = ("DatabaseReconciler",)

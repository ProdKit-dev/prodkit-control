from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class RegistryReconciler(MappingReconciler):
    """Normalize package/container registry records for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("registry")

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
                "observation_id": f"manifest:{record['digest']}",
                "observed_at": record.get("pushed_at", collected_at),
                "action_id": record.get("action_id"),
                "external_reference": record.get("url"),
                "state": {
                    "repository": record.get("repository"),
                    "tag": record.get("tag"),
                    "digest": record["digest"],
                    "immutable": record.get("immutable", False),
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


__all__ = ("RegistryReconciler",)

from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class DeploymentReconciler(MappingReconciler):
    """Normalize deployment-system records for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("deployment")

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
                "observation_id": f"deployment:{record['deployment_id']}",
                "observed_at": record.get("deployed_at", collected_at),
                "action_id": record.get("action_id"),
                "external_reference": record.get("url"),
                "state": {
                    "deployment_id": record["deployment_id"],
                    "environment": record.get("environment"),
                    "revision": record.get("revision"),
                    "status": record.get("status"),
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


__all__ = ("DeploymentReconciler",)

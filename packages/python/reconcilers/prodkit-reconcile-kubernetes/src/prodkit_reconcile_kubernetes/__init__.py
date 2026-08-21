from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class KubernetesReconciler(MappingReconciler):
    """Normalize Kubernetes workload objects for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("kubernetes")

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
            metadata = record.get("metadata")
            status = record.get("status")
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            status_dict = status if isinstance(status, dict) else {}
            annotations = metadata_dict.get("annotations")
            annotation_dict = annotations if isinstance(annotations, dict) else {}
            uid = str(metadata_dict.get("uid", metadata_dict.get("name", "unknown")))
            normalized.append(
                {
                    "record_type": "state",
                    "observation_id": f"workload:{uid}",
                    "observed_at": record.get("observed_at", collected_at),
                    "action_id": annotation_dict.get("prodkit.dev/action-id"),
                    "external_reference": record.get("url"),
                    "state": {
                        "apiVersion": record.get("apiVersion"),
                        "kind": record.get("kind"),
                        "metadata": metadata_dict,
                        "status": status_dict,
                    },
                }
            )
        return self.normalize(
            tenant_id=tenant_id,
            records=normalized,
            collected_at=collected_at,
            cursor=cursor,
        )


__all__ = ("KubernetesReconciler",)

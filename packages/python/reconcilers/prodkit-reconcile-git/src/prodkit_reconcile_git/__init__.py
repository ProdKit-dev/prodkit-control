from datetime import datetime

from prodkit_control_core import ReconciliationBatch
from prodkit_control_core.reconciliation.adapters import MappingReconciler


class GitReconciler(MappingReconciler):
    """Normalize Git commit/ref evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("git")

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
            sha = str(record["commit_sha"])
            ref = str(record["ref"])
            observed_at = record.get("committed_at", collected_at)
            normalized.append(
                {
                    "record_type": "state",
                    "observation_id": f"commit:{sha}",
                    "observed_at": observed_at,
                    "action_id": record.get("action_id"),
                    "external_reference": f"{ref}@{sha}",
                    "state": {"commit_sha": sha, "ref": ref},
                }
            )
            normalized.append(
                {
                    "record_type": "audit",
                    "event_id": f"ref-update:{ref}:{sha}",
                    "occurred_at": observed_at,
                    "event_type": "git.ref.update",
                    "actor": record.get("actor"),
                    "action_id": record.get("action_id"),
                    "resource": ref,
                    "external_reference": f"{ref}@{sha}",
                    "payload": {"ref": ref, "commit_sha": sha},
                }
            )
        return self.normalize(
            tenant_id=tenant_id,
            records=normalized,
            collected_at=collected_at,
            cursor=cursor,
        )


__all__ = ("GitReconciler",)

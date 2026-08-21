from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from prodkit_control_core import ProductionCompletenessProfile
from prodkit_reconcile_ci import CIReconciler
from prodkit_reconcile_database import DatabaseReconciler
from prodkit_reconcile_deployment import DeploymentReconciler
from prodkit_reconcile_git import GitReconciler
from prodkit_reconcile_github import GitHubReconciler
from prodkit_reconcile_kubernetes import KubernetesReconciler
from prodkit_reconcile_registry import RegistryReconciler


def test_source_shaped_provider_fixtures_normalize_without_losing_source_identity(tenant_id):
    fixture_path = (
        Path(__file__).parent / "fixtures/reconciliation/source_shaped_provider_records.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    adapters = {
        "git": GitReconciler(),
        "github": GitHubReconciler(),
        "ci": CIReconciler(),
        "registry": RegistryReconciler(),
        "deployment": DeploymentReconciler(),
        "kubernetes": KubernetesReconciler(),
        "database": DatabaseReconciler(),
    }
    assert set(payload) == set(adapters)
    now = datetime.now(UTC)

    for source, adapter in adapters.items():
        batch = adapter.normalize_provider_records(
            tenant_id=tenant_id,
            records=payload[source],
            collected_at=now,
            cursor="fixture-page-2",
        )
        assert batch.source_system == source
        assert batch.tenant_id == tenant_id
        assert batch.cursor == "fixture-page-2"
        assert batch.high_watermark is not None
        assert batch.observations or batch.audit_events

    github = adapters["github"].normalize_provider_records(
        tenant_id=tenant_id,
        records=payload["github"],
        collected_at=now,
    )
    assert len(github.observations) == 1
    assert len(github.audit_events) == 1
    assert github.audit_events[0].event_type == "deployment.created"


def test_production_completeness_profile_can_be_organization_scoped(tenant_id):
    profile = ProductionCompletenessProfile(
        profile_id="org-production",
        tenant_id=tenant_id,
        organization_id="organization-42",
        required_sources=("github", "registry", "kubernetes"),
    )
    assert profile.organization_id == "organization-42"

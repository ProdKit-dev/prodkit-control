from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from prodkit_control_core import (
    IntegrityViolationError,
    LineageGraph,
    ProductionObservationNode,
    RunStatus,
    sha256_hex,
)
from prodkit_control_runtime import (
    EvidenceBundleBuilder,
    EvidenceBundleVerifier,
    InMemoryEventLedger,
    RunCoordinator,
    evidence_bundle_sha256,
)


@pytest.mark.asyncio
async def test_evidence_bundle_roundtrip(tmp_path: Path, human) -> None:
    ledger = InMemoryEventLedger()
    coordinator = RunCoordinator(ledger)
    run = await coordinator.start_run(
        tenant_id=human.tenant_id,
        initiated_by=human,
        environment="test",
        purpose="bundle",
    )
    await coordinator.complete_run(run.run_id, actor=human, status=RunStatus.SUCCEEDED)
    observation = ProductionObservationNode(
        node_id=uuid4(),
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        digest=sha256_hex("state"),
        recorded_at=datetime.now(UTC),
        observation_id=uuid4(),
        environment="production",
        observer_identity="observer",
        observed_at=datetime.now(UTC),
    )
    lineage = LineageGraph(
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        nodes=(observation,),
    )
    archive = await EvidenceBundleBuilder(ledger).build(
        run.run_id,
        tmp_path / "bundle",
        lineage=lineage,
    )
    digest = evidence_bundle_sha256(archive)
    manifest = EvidenceBundleVerifier().verify(
        archive,
        expected_archive_sha256=digest,
    )
    assert manifest["run_id"] == str(run.run_id)
    assert manifest["event_count"] == 2
    assert manifest["lineage_node_count"] == 1
    assert manifest["lineage_relation_count"] == 0

    with pytest.raises(IntegrityViolationError, match="external trust anchor"):
        EvidenceBundleVerifier().verify(archive, expected_archive_sha256="0" * 64)

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from prodkit_control_core import ProductionObservationNode, sha256_hex
from prodkit_control_fastapi import create_app


@pytest.mark.asyncio
async def test_start_and_read_run() -> None:
    headers = {"x-prodkit-tenant-id": "tenant-api"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/runs",
            headers=headers,
            json={"purpose": "api test", "actor_id": "user-1"},
        )
        assert response.status_code == 201
        run = response.json()
        fetched = await client.get(f"/v1/runs/{run['run_id']}", headers=headers)
        assert fetched.status_code == 200
        events = await client.get(f"/v1/runs/{run['run_id']}/events", headers=headers)
        assert events.status_code == 200
        assert events.json()[0]["event_type"] == "run.started"


@pytest.mark.asyncio
async def test_record_and_assess_lineage() -> None:
    headers = {"x-prodkit-tenant-id": "tenant-api"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        run = (
            await client.post(
                "/v1/runs",
                headers=headers,
                json={"purpose": "lineage api test", "actor_id": "user-1"},
            )
        ).json()
        node = ProductionObservationNode(
            node_id=uuid4(),
            run_id=run["run_id"],
            tenant_id="tenant-api",
            digest=sha256_hex("production-state"),
            recorded_at=datetime.now(UTC),
            observation_id=uuid4(),
            environment="production",
            observer_identity="observer",
            observed_at=datetime.now(UTC),
        )

        recorded = await client.post(
            f"/v1/runs/{run['run_id']}/lineage/nodes",
            headers=headers,
            json={"node": node.model_dump(mode="json"), "actor_id": "recorder"},
        )
        assert recorded.status_code == 201
        anchored_run = (await client.get(f"/v1/runs/{run['run_id']}", headers=headers)).json()
        assert anchored_run["lineage_graph_digest"]
        graph = await client.get(f"/v1/runs/{run['run_id']}/lineage", headers=headers)
        assert graph.status_code == 200
        assert len(graph.json()["nodes"]) == 1

        assessed = await client.post(
            f"/v1/runs/{run['run_id']}/lineage:assess",
            headers=headers,
            json={"observation_id": str(node.node_id)},
        )
        assert assessed.status_code == 200
        assert assessed.json()["complete"] is False
        enforced = await client.post(
            f"/v1/runs/{run['run_id']}/lineage:assess",
            headers=headers,
            json={"observation_id": str(node.node_id), "enforce": True},
        )
        assert enforced.status_code == 409

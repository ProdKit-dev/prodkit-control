from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from prodkit_control_core import ProductionObservationNode, sha256_hex
from prodkit_control_fastapi import create_app


def auth_headers(*, actor_id: str = "user-1", actor_kind: str = "human") -> dict[str, str]:
    return {
        "x-prodkit-tenant-id": "tenant-api",
        "x-prodkit-actor-id": actor_id,
        "x-prodkit-actor-kind": actor_kind,
    }


@pytest.mark.asyncio
async def test_protected_routes_fail_closed_without_auth_configuration() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 503
        response = await client.post("/v1/runs", json={"purpose": "blocked"})
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_start_and_read_run() -> None:
    headers = auth_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(allow_insecure_header_auth=True)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/runs",
            headers=headers,
            json={"purpose": "api test"},
        )
        assert response.status_code == 201
        run = response.json()
        assert run["initiated_by"]["id"] == "user-1"
        fetched = await client.get(f"/v1/runs/{run['run_id']}", headers=headers)
        assert fetched.status_code == 200
        events = await client.get(f"/v1/runs/{run['run_id']}/events", headers=headers)
        assert events.status_code == 200
        assert events.json()[0]["event_type"] == "run.started"


@pytest.mark.asyncio
async def test_draining_replica_fails_readiness_and_new_work_admission() -> None:
    app = create_app(allow_insecure_header_auth=True)
    await app.state.services.lifecycle.begin_draining()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        ready = await client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["detail"] == {"status": "not_ready", "reason": "draining"}

        rejected = await client.post(
            "/v1/runs",
            headers=auth_headers(),
            json={"purpose": "must not enter a draining replica"},
        )
        assert rejected.status_code == 503
        assert rejected.headers["retry-after"] == "1"
        assert rejected.json()["detail"]["code"] == "runtime_draining"


@pytest.mark.asyncio
async def test_record_and_assess_lineage() -> None:
    headers = auth_headers(actor_id="recorder", actor_kind="service")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(allow_insecure_header_auth=True)),
        base_url="http://test",
    ) as client:
        run = (
            await client.post(
                "/v1/runs",
                headers=headers,
                json={"purpose": "lineage api test"},
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
            json={"node": node.model_dump(mode="json")},
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

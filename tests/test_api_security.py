from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodkit_control_core import RateLimitPolicy
from prodkit_control_fastapi import add_security_rate_limit
from prodkit_control_runtime import SlidingWindowRateLimiter


def test_security_rate_limit_middleware_blocks_abuse_and_exempts_health() -> None:
    now = [100.0]
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/protected")
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    add_security_rate_limit(
        app,
        limiter=SlidingWindowRateLimiter(
            RateLimitPolicy(policy_id="api", limit=2, window_seconds=10, max_keys=100),
            clock=lambda: now[0],
        ),
        key_resolver=lambda _request: "tenant-a:principal-a",
    )

    with TestClient(app) as client:
        first = client.get("/protected")
        second = client.get("/protected")
        denied = client.get("/protected")
        health = client.get("/healthz")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert second.status_code == 200
    assert second.headers["x-ratelimit-remaining"] == "0"
    assert denied.status_code == 429
    assert denied.headers["retry-after"] == "10"
    assert denied.json()["detail"]["code"] == "rate_limit_exceeded"
    assert health.status_code == 200

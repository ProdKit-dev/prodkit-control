from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from fastapi import FastAPI, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from prodkit_control_runtime import SlidingWindowRateLimiter


class RateLimitKeyResolver(Protocol):
    def __call__(self, request: Request) -> str: ...


def direct_client_rate_limit_key(request: Request) -> str:
    """Use the socket peer only; forwarded headers are untrusted unless a deployment terminates them."""

    client = request.client
    if client is None or not client.host:
        return "unknown-client"
    return client.host


class SecurityRateLimitMiddleware(BaseHTTPMiddleware):
    """Fail-closed HTTP abuse backstop for a single API process.

    Multi-replica production deployments should enforce an equivalent shared limit at the trusted
    ingress/gateway and may retain this middleware as a per-process defense-in-depth ceiling.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowRateLimiter,
        key_resolver: RateLimitKeyResolver = direct_client_rate_limit_key,
        exempt_paths: Iterable[str] = ("/healthz", "/readyz"),
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._key_resolver = key_resolver
        self._exempt_paths = frozenset(exempt_paths)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._exempt_paths:
            return await call_next(request)
        decision = self._limiter.check(self._key_resolver(request))
        if not decision.allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": {
                        "code": "rate_limit_exceeded",
                        "message": "request rate exceeds configured abuse-control policy",
                    }
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response


def add_security_rate_limit(
    app: FastAPI,
    *,
    limiter: SlidingWindowRateLimiter,
    key_resolver: RateLimitKeyResolver = direct_client_rate_limit_key,
    exempt_paths: Iterable[str] = ("/healthz", "/readyz"),
) -> None:
    """Attach the reference single-process limiter to a FastAPI application."""

    app.add_middleware(
        SecurityRateLimitMiddleware,
        limiter=limiter,
        key_resolver=key_resolver,
        exempt_paths=tuple(exempt_paths),
    )

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from prodkit_control_core import ActorKind

from .app import RequestPrincipal


@dataclass(frozen=True)
class RoleAuthorizationPolicy:
    """Explicit API capability-to-role policy used after authentication and tenant scoping."""

    capabilities: Mapping[str, frozenset[str]]

    def require(self, principal: RequestPrincipal, capability: str) -> None:
        required = self.capabilities.get(capability, frozenset())
        if not required:
            return
        if required.isdisjoint(principal.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "insufficient_role", "capability": capability},
            )


class OIDCPrincipalResolver:
    """OIDC/JWT resolver validating issuer, audience, signature, expiry, and identity claims."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        actor_kind_claim: str = "actor_kind",
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        leeway_seconds: int = 30,
    ) -> None:
        if not issuer.startswith("https://") or not jwks_url.startswith("https://"):
            raise ValueError("OIDC issuer and JWKS URL must use HTTPS")
        if not algorithms or any(algorithm not in {"RS256", "ES256"} for algorithm in algorithms):
            raise ValueError("OIDC resolver only permits RS256/ES256")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._actor_kind_claim = actor_kind_claim
        self._algorithms = algorithms
        self._leeway = leeway_seconds
        self._jwks = PyJWKClient(jwks_url, cache_keys=True)

    async def __call__(self, request: Request) -> RequestPrincipal:
        token = self._bearer_token(request)
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_bearer_token"},
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return self._principal_from_claims(claims)

    def _principal_from_claims(self, claims: Mapping[str, Any]) -> RequestPrincipal:
        tenant = claims.get(self._tenant_claim)
        subject = claims.get("sub")
        if not isinstance(tenant, str) or not tenant or not isinstance(subject, str) or not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "missing_required_identity_claim"},
            )
        raw_roles = claims.get(self._roles_claim, ())
        if isinstance(raw_roles, str):
            roles = tuple(role for role in raw_roles.split() if role)
        elif isinstance(raw_roles, list) and all(isinstance(role, str) for role in raw_roles):
            roles = tuple(raw_roles)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_roles_claim"},
            )
        raw_kind = claims.get(self._actor_kind_claim, ActorKind.SERVICE.value)
        try:
            actor_kind = ActorKind(str(raw_kind))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_actor_kind_claim"},
            ) from exc
        display_name = claims.get("name")
        return RequestPrincipal(
            tenant_id=tenant,
            actor_id=subject,
            actor_kind=actor_kind,
            display_name=display_name if isinstance(display_name, str) else None,
            roles=roles,
        )

    @staticmethod
    def _bearer_token(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "bearer_token_required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token.strip()

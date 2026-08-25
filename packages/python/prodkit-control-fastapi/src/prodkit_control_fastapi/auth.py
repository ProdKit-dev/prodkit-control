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
    """OIDC/JWT resolver with bounded lifetime and explicit workload/client binding."""

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
        max_token_lifetime_seconds: int = 900,
        require_not_before: bool = True,
        allowed_authorized_parties: tuple[str, ...] = (),
    ) -> None:
        if not issuer.startswith("https://") or not jwks_url.startswith("https://"):
            raise ValueError("OIDC issuer and JWKS URL must use HTTPS")
        if not algorithms or any(algorithm not in {"RS256", "ES256"} for algorithm in algorithms):
            raise ValueError("OIDC resolver only permits RS256/ES256")
        if leeway_seconds < 0 or leeway_seconds > 120:
            raise ValueError("OIDC leeway must be between 0 and 120 seconds")
        if max_token_lifetime_seconds < 30 or max_token_lifetime_seconds > 3600:
            raise ValueError("OIDC maximum token lifetime must be between 30 and 3600 seconds")
        if len(allowed_authorized_parties) != len(set(allowed_authorized_parties)):
            raise ValueError("OIDC authorized parties must be unique")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._actor_kind_claim = actor_kind_claim
        self._algorithms = algorithms
        self._leeway = leeway_seconds
        self._max_token_lifetime = max_token_lifetime_seconds
        self._require_not_before = require_not_before
        self._allowed_authorized_parties = frozenset(allowed_authorized_parties)
        self._jwks = PyJWKClient(jwks_url, cache_keys=True)

    async def __call__(self, request: Request) -> RequestPrincipal:
        token = self._bearer_token(request)
        required_claims = ["exp", "iat", "sub"]
        if self._require_not_before:
            required_claims.append("nbf")
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": required_claims},
            )
            self._validate_lifetime_and_client(claims)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_bearer_token"},
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return self._principal_from_claims(claims)

    def _validate_lifetime_and_client(self, claims: Mapping[str, Any]) -> None:
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            isinstance(issued_at, bool)
            or isinstance(expires_at, bool)
            or not isinstance(issued_at, (int, float))
            or not isinstance(expires_at, (int, float))
        ):
            raise ValueError("OIDC temporal claims must be NumericDate values")
        if expires_at <= issued_at:
            raise ValueError("OIDC expiry must follow issuance")
        if expires_at - issued_at > self._max_token_lifetime:
            raise ValueError("OIDC token lifetime exceeds policy")
        if self._allowed_authorized_parties:
            authorized_party = claims.get("azp")
            if not isinstance(authorized_party, str):
                raise ValueError("OIDC azp claim is required by client-binding policy")
            if authorized_party not in self._allowed_authorized_parties:
                raise ValueError("OIDC azp is not allowed")

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

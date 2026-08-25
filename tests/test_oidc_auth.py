from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request

from prodkit_control_core import ActorKind
from prodkit_control_fastapi.auth import OIDCPrincipalResolver

ISSUER = "https://issuer.example"
AUDIENCE = "prodkit-control"
JWKS_URL = "https://issuer.example/.well-known/jwks.json"


class StaticSigningKeyProvider:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        assert token
        return SimpleNamespace(key=self._public_key)


def _resolver(
    public_key: rsa.RSAPublicKey,
    *,
    allowed_authorized_parties: tuple[str, ...] = (),
) -> OIDCPrincipalResolver:
    resolver = OIDCPrincipalResolver(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=JWKS_URL,
        algorithms=("RS256",),
        leeway_seconds=0,
        max_token_lifetime_seconds=600,
        require_not_before=True,
        allowed_authorized_parties=allowed_authorized_parties,
    )
    resolver._jwks = StaticSigningKeyProvider(public_key)  # type: ignore[assignment]
    return resolver


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "service-42",
        "tenant_id": "tenant-a",
        "roles": ["operator", "production_approver"],
        "actor_kind": ActorKind.SERVICE.value,
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


@pytest.mark.asyncio
async def test_oidc_resolver_accepts_verified_tenant_scoped_identity() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), private_key, algorithm="RS256")

    principal = await _resolver(private_key.public_key())(_request(token))

    assert principal.tenant_id == "tenant-a"
    assert principal.actor_id == "service-42"
    assert principal.actor_kind is ActorKind.SERVICE
    assert set(principal.roles) == {"operator", "production_approver"}


@pytest.mark.asyncio
async def test_oidc_resolver_rejects_token_signed_by_untrusted_key() -> None:
    trusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(_claims(tenant_id="tenant-b"), attacker_key, algorithm="RS256")

    with pytest.raises(HTTPException) as error:
        await _resolver(trusted_key.public_key())(_request(forged))

    assert error.value.status_code == 401
    assert error.value.detail == {"code": "invalid_bearer_token"}


@pytest.mark.asyncio
async def test_oidc_resolver_rejects_signed_identity_without_tenant_binding() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = _claims()
    del claims["tenant_id"]
    token = jwt.encode(claims, private_key, algorithm="RS256")

    with pytest.raises(HTTPException) as error:
        await _resolver(private_key.public_key())(_request(token))

    assert error.value.status_code == 401
    assert error.value.detail == {"code": "missing_required_identity_claim"}


@pytest.mark.asyncio
async def test_oidc_resolver_rejects_missing_not_before() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = _claims()
    del claims["nbf"]
    token = jwt.encode(claims, private_key, algorithm="RS256")

    with pytest.raises(HTTPException) as error:
        await _resolver(private_key.public_key())(_request(token))

    assert error.value.status_code == 401
    assert error.value.detail == {"code": "invalid_bearer_token"}


@pytest.mark.asyncio
async def test_oidc_resolver_rejects_overlong_token_lifetime() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    token = jwt.encode(
        _claims(iat=now, nbf=now, exp=now + timedelta(minutes=20)),
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(HTTPException) as error:
        await _resolver(private_key.public_key())(_request(token))

    assert error.value.status_code == 401
    assert error.value.detail == {"code": "invalid_bearer_token"}


@pytest.mark.asyncio
async def test_oidc_resolver_enforces_authorized_party_binding() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    accepted = jwt.encode(_claims(azp="trusted-client"), private_key, algorithm="RS256")
    rejected = jwt.encode(_claims(azp="other-client"), private_key, algorithm="RS256")
    resolver = _resolver(private_key.public_key(), allowed_authorized_parties=("trusted-client",))

    principal = await resolver(_request(accepted))
    assert principal.actor_id == "service-42"

    with pytest.raises(HTTPException) as error:
        await resolver(_request(rejected))
    assert error.value.status_code == 401

"""Tests for agent-side license validation logic."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aems_agent import license_validation as module


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        if url not in self._responses:
            raise RuntimeError(f"unexpected url {url}")
        return self._responses[url]


def _make_token_and_jwks(refresh_after_offset_seconds: int) -> tuple[str, dict[str, Any]]:
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://license.example.com",
        "aud": "aems-agent",
        "sub": "cus_123",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(days=365)).timestamp()),
        "jti": "jti-123",
        "tier": "personal",
        "seats": 1,
        "offline_grace_days": 30,
        "refresh_after": int(now.timestamp()) + refresh_after_offset_seconds,
        "email": "user@example.com",
    }
    token = jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers={"kid": "kid-1"},
    )

    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    x = jwt.utils.base64url_encode(raw).decode("ascii")
    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": "kid-1",
                "x": x,
            }
        ]
    }
    return token, jwks


@pytest.mark.asyncio
async def test_validate_license_token_offline_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    token, jwks = _make_token_and_jwks(refresh_after_offset_seconds=3600)
    responses = {
        "https://license.example.com/.well-known/jwks.json": _FakeResponse(200, jwks),
    }
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(responses),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
    )
    assert result.valid is True
    assert result.reason == "valid_offline"
    assert result.heartbeat_checked is False


@pytest.mark.asyncio
async def test_validate_license_token_revoked_on_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, jwks = _make_token_and_jwks(refresh_after_offset_seconds=-10)
    responses = {
        "https://license.example.com/.well-known/jwks.json": _FakeResponse(200, jwks),
        "https://license.example.com/api/licenses/jti-123/status": _FakeResponse(
            200,
            {
                "jti": "jti-123",
                "tier": "personal",
                "seats": 1,
                "revoked": True,
                "expires_at": "2030-01-01T00:00:00+00:00",
            },
        ),
    }
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(responses),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
    )
    assert result.valid is False
    assert result.reason == "license_revoked"
    assert result.heartbeat_checked is True


@pytest.mark.asyncio
async def test_validate_license_token_invalid_header_returns_reason() -> None:
    result = await module.validate_license_token(
        token="not-a-jwt",
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
    )
    assert result.valid is False
    assert result.reason == "invalid_token_header"


@pytest.mark.asyncio
async def test_validate_license_token_heartbeat_server_error_within_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, jwks = _make_token_and_jwks(refresh_after_offset_seconds=-10)
    responses = {
        "https://license.example.com/.well-known/jwks.json": _FakeResponse(200, jwks),
        "https://license.example.com/api/licenses/jti-123/status": _FakeResponse(503, {}),
    }
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(responses),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
    )
    assert result.valid is True
    assert result.reason == "heartbeat_unreachable_within_grace"
    assert result.heartbeat_checked is True


@pytest.mark.asyncio
async def test_validate_license_token_heartbeat_server_error_grace_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, jwks = _make_token_and_jwks(refresh_after_offset_seconds=-(40 * 86400))
    responses = {
        "https://license.example.com/.well-known/jwks.json": _FakeResponse(200, jwks),
        "https://license.example.com/api/licenses/jti-123/status": _FakeResponse(503, {}),
    }
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(responses),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
    )
    assert result.valid is False
    assert result.reason == "heartbeat_unreachable_grace_expired"
    assert result.heartbeat_checked is True


class _FakeAsyncClientFetchFails:
    """AsyncClient that always raises on get (simulates network failure)."""

    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClientFetchFails":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        raise RuntimeError("network down")


@pytest.mark.asyncio
async def test_jwks_cache_fallback_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JWKS fetch fails but cached JWKS file exists → validation proceeds."""
    token, jwks = _make_token_and_jwks(refresh_after_offset_seconds=3600)
    # Pre-populate cache
    cache_file = tmp_path / "jwks.json"
    cache_file.write_text(json.dumps(jwks), encoding="utf-8")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClientFetchFails(),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
        jwks_cache_dir=tmp_path,
    )
    assert result.valid is True
    assert result.reason == "valid_offline"


@pytest.mark.asyncio
async def test_jwks_fetch_failure_no_cache_returns_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JWKS fetch fails and no cache → valid=False."""
    token, _jwks = _make_token_and_jwks(refresh_after_offset_seconds=3600)

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClientFetchFails(),
    )

    result = await module.validate_license_token(
        token=token,
        license_service_url="https://license.example.com",
        issuer="https://license.example.com",
        audience="aems-agent",
        jwks_cache_dir=tmp_path,
    )
    assert result.valid is False
    assert result.reason == "jwks_fetch_failed"

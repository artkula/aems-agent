"""License token validation and heartbeat checks against license-service."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jwt import exceptions as jwt_exceptions

from .config import get_config_dir
from .device_id import get_device_id


@dataclass(frozen=True)
class LicenseValidationResult:
    """Result of validating one license token."""

    valid: bool
    reason: str
    jti: str
    heartbeat_checked: bool
    heartbeat_ok: bool
    revoked: bool
    tier: str
    seats: int
    expires_at: int


async def validate_license_token(
    *,
    token: str,
    license_service_url: str,
    issuer: str,
    audience: str,
    jwks_cache_dir: Path | None = None,
    timeout_seconds: float = 5.0,
    clock_skew_seconds: int = 300,
) -> LicenseValidationResult:
    """Validate JWT signature/claims and run heartbeat logic."""
    if not token.strip():
        return invalid_license_result("empty_token")
    if not license_service_url.strip():
        return invalid_license_result("missing_license_service_url")
    if not issuer.strip():
        return invalid_license_result("missing_license_issuer")
    if not audience.strip():
        return invalid_license_result("missing_license_audience")

    try:
        header = jwt.get_unverified_header(token)
    except jwt_exceptions.PyJWTError:
        return invalid_license_result("invalid_token_header")

    kid = str(header.get("kid", "")).strip()
    if not kid:
        return invalid_license_result("missing_kid")

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        jwks_url = urljoin(_normalize_base_url(license_service_url), "/.well-known/jwks.json")
        jwks = None
        cache_dir = jwks_cache_dir or get_config_dir()
        jwks_cache_file = cache_dir / "jwks.json"

        try:
            jwks_resp = await client.get(jwks_url)
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()
            # Cache it for offline fallback
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                jwks_cache_file.write_text(json.dumps(jwks), encoding="utf-8")
            except Exception:
                pass
        except Exception:
            # Fallback to local cache if offline
            if jwks_cache_file.exists():
                try:
                    jwks = json.loads(jwks_cache_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

        if not jwks:
            return invalid_license_result("jwks_fetch_failed")

        public_key = _public_key_from_jwks(jwks, kid)
        if public_key is None:
            return invalid_license_result("kid_not_found")

        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["EdDSA"],
                audience=audience,
                issuer=issuer,
                leeway=clock_skew_seconds,
            )
        except jwt_exceptions.ExpiredSignatureError:
            return invalid_license_result("token_expired")
        except jwt_exceptions.ImmatureSignatureError:
            return invalid_license_result("token_immature")
        except jwt_exceptions.InvalidAudienceError:
            return invalid_license_result("invalid_audience")
        except jwt_exceptions.InvalidIssuerError:
            return invalid_license_result("invalid_issuer")
        except jwt_exceptions.PyJWTError:
            return invalid_license_result("invalid_token")

        return await _run_heartbeat_logic(
            client=client,
            claims=claims,
            license_service_url=license_service_url,
        )


def validate_license_token_sync(
    *,
    token: str,
    license_service_url: str,
    issuer: str,
    audience: str,
    jwks_cache_dir: Path | None = None,
    timeout_seconds: float = 5.0,
    clock_skew_seconds: int = 300,
) -> LicenseValidationResult:
    """Synchronous wrapper for CLI usage."""
    return asyncio.run(
        validate_license_token(
            token=token,
            license_service_url=license_service_url,
            issuer=issuer,
            audience=audience,
            jwks_cache_dir=jwks_cache_dir,
            timeout_seconds=timeout_seconds,
            clock_skew_seconds=clock_skew_seconds,
        )
    )


async def _run_heartbeat_logic(
    *,
    client: httpx.AsyncClient,
    claims: dict[str, Any],
    license_service_url: str,
) -> LicenseValidationResult:
    jti = str(claims.get("jti", ""))
    tier = str(claims.get("tier", ""))
    seats = int(claims.get("seats", 0))
    exp = int(claims.get("exp", 0))
    refresh_after = int(claims.get("refresh_after", 0))
    offline_grace_days = int(claims.get("offline_grace_days", 0))

    now_ts = int(datetime.now(timezone.utc).timestamp())
    requires_heartbeat = now_ts >= refresh_after
    heartbeat_deadline = refresh_after + (offline_grace_days * 86400)

    if not requires_heartbeat:
        return LicenseValidationResult(
            valid=True,
            reason="valid_offline",
            jti=jti,
            heartbeat_checked=False,
            heartbeat_ok=False,
            revoked=False,
            tier=tier,
            seats=seats,
            expires_at=exp,
        )

    status_url = urljoin(_normalize_base_url(license_service_url), f"/api/licenses/{jti}/status")
    try:
        status_resp = await client.get(status_url, params={"device_id": get_device_id()})
    except Exception:
        return _heartbeat_failure_result(
            now_ts=now_ts,
            heartbeat_deadline=heartbeat_deadline,
            jti=jti,
            tier=tier,
            seats=seats,
            exp=exp,
        )

    if status_resp.status_code == 404:
        return LicenseValidationResult(
            valid=False,
            reason="license_not_found",
            jti=jti,
            heartbeat_checked=True,
            heartbeat_ok=False,
            revoked=False,
            tier=tier,
            seats=seats,
            expires_at=exp,
        )
    if status_resp.status_code >= 500:
        return _heartbeat_failure_result(
            now_ts=now_ts,
            heartbeat_deadline=heartbeat_deadline,
            jti=jti,
            tier=tier,
            seats=seats,
            exp=exp,
        )
    if status_resp.status_code >= 400:
        return LicenseValidationResult(
            valid=False,
            reason=f"heartbeat_http_{status_resp.status_code}",
            jti=jti,
            heartbeat_checked=True,
            heartbeat_ok=False,
            revoked=False,
            tier=tier,
            seats=seats,
            expires_at=exp,
        )
    try:
        payload = status_resp.json()
    except Exception:
        return _heartbeat_failure_result(
            now_ts=now_ts,
            heartbeat_deadline=heartbeat_deadline,
            jti=jti,
            tier=tier,
            seats=seats,
            exp=exp,
        )
    revoked = bool(payload.get("revoked"))
    if revoked:
        return LicenseValidationResult(
            valid=False,
            reason="license_revoked",
            jti=jti,
            heartbeat_checked=True,
            heartbeat_ok=True,
            revoked=True,
            tier=tier,
            seats=seats,
            expires_at=exp,
        )
    return LicenseValidationResult(
        valid=True,
        reason="heartbeat_ok",
        jti=jti,
        heartbeat_checked=True,
        heartbeat_ok=True,
        revoked=False,
        tier=tier,
        seats=seats,
        expires_at=exp,
    )


def _public_key_from_jwks(jwks: dict[str, Any], kid: str) -> Ed25519PublicKey | None:
    for row in jwks.get("keys", []):
        if str(row.get("kid", "")) != kid:
            continue
        if str(row.get("kty", "")) != "OKP" or str(row.get("crv", "")) != "Ed25519":
            continue
        raw = str(row.get("x", ""))
        if not raw:
            continue
        # Base64url decode with explicit padding.
        padded = raw + "=" * ((4 - len(raw) % 4) % 4)
        try:
            key_bytes = jwt.utils.base64url_decode(padded.encode("ascii"))
        except Exception:
            return None
        try:
            return Ed25519PublicKey.from_public_bytes(key_bytes)
        except ValueError:
            return None
    return None


def _heartbeat_failure_result(
    *,
    now_ts: int,
    heartbeat_deadline: int,
    jti: str,
    tier: str,
    seats: int,
    exp: int,
) -> LicenseValidationResult:
    if now_ts <= heartbeat_deadline:
        return LicenseValidationResult(
            valid=True,
            reason="heartbeat_unreachable_within_grace",
            jti=jti,
            heartbeat_checked=True,
            heartbeat_ok=False,
            revoked=False,
            tier=tier,
            seats=seats,
            expires_at=exp,
        )
    return LicenseValidationResult(
        valid=False,
        reason="heartbeat_unreachable_grace_expired",
        jti=jti,
        heartbeat_checked=True,
        heartbeat_ok=False,
        revoked=False,
        tier=tier,
        seats=seats,
        expires_at=exp,
    )


def invalid_license_result(reason: str) -> LicenseValidationResult:
    return LicenseValidationResult(
        valid=False,
        reason=reason,
        jti="",
        heartbeat_checked=False,
        heartbeat_ok=False,
        revoked=False,
        tier="",
        seats=0,
        expires_at=0,
    )


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"

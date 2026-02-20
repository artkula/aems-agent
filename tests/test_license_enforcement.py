"""Tests for runtime license enforcement policy behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from aems_agent.config import AgentConfig, save_license_token
from aems_agent.license_enforcement import (
    LICENSE_POLICY_HARD_BLOCK,
    LICENSE_POLICY_SOFT_BLOCK,
    LICENSE_POLICY_WARN,
    LicenseEnforcementController,
    evaluate_license_policy,
)
from aems_agent.license_validation import LicenseValidationResult


def _result(valid: bool, reason: str) -> LicenseValidationResult:
    return LicenseValidationResult(
        valid=valid,
        reason=reason,
        jti="jti-1",
        heartbeat_checked=True,
        heartbeat_ok=valid,
        revoked=not valid,
        tier="personal",
        seats=1,
        expires_at=9999999999,
    )


def test_evaluate_policy_warn_allows_startup_with_invalid_license() -> None:
    decision = evaluate_license_policy(
        policy_mode=LICENSE_POLICY_WARN,
        validation_result=_result(False, "license_revoked"),
    )
    assert decision.allow_startup is True
    assert decision.limited_mode_active is False
    assert decision.should_exit_hard_block is False


def test_evaluate_policy_soft_block_enables_limited_mode() -> None:
    decision = evaluate_license_policy(
        policy_mode=LICENSE_POLICY_SOFT_BLOCK,
        validation_result=_result(False, "license_revoked"),
    )
    assert decision.allow_startup is True
    assert decision.limited_mode_active is True
    assert decision.should_exit_hard_block is False


def test_evaluate_policy_hard_block_disallows_startup() -> None:
    decision = evaluate_license_policy(
        policy_mode=LICENSE_POLICY_HARD_BLOCK,
        validation_result=_result(False, "license_revoked"),
    )
    assert decision.allow_startup is False
    assert decision.should_exit_hard_block is True


@pytest.mark.asyncio
async def test_controller_startup_soft_block_sets_limited_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aems_agent import license_enforcement as module

    async def fake_validate_license_token(**_kwargs: object) -> LicenseValidationResult:
        return _result(False, "license_revoked")

    monkeypatch.setattr(module, "validate_license_token", fake_validate_license_token)
    save_license_token("token-123", tmp_path)
    config = AgentConfig(
        license_enforcement_mode=LICENSE_POLICY_SOFT_BLOCK,
        license_service_url="https://license.example.com",
        license_issuer="https://license.example.com",
    )
    controller = LicenseEnforcementController(config_dir=tmp_path, config=config)
    await controller.startup_check()

    snapshot = controller.snapshot()
    assert snapshot.policy_mode == LICENSE_POLICY_SOFT_BLOCK
    assert snapshot.limited_mode_active is True
    assert snapshot.last_valid is False
    assert snapshot.last_reason == "license_revoked"


@pytest.mark.asyncio
async def test_controller_startup_warn_keeps_full_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aems_agent import license_enforcement as module

    async def fake_validate_license_token(**_kwargs: object) -> LicenseValidationResult:
        return _result(False, "license_revoked")

    monkeypatch.setattr(module, "validate_license_token", fake_validate_license_token)
    save_license_token("token-123", tmp_path)
    config = AgentConfig(
        license_enforcement_mode=LICENSE_POLICY_WARN,
        license_service_url="https://license.example.com",
        license_issuer="https://license.example.com",
    )
    controller = LicenseEnforcementController(config_dir=tmp_path, config=config)
    await controller.startup_check()

    snapshot = controller.snapshot()
    assert snapshot.policy_mode == LICENSE_POLICY_WARN
    assert snapshot.limited_mode_active is False
    assert snapshot.last_valid is False


@pytest.mark.asyncio
async def test_controller_startup_hard_block_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aems_agent import license_enforcement as module

    async def fake_validate_license_token(**_kwargs: object) -> LicenseValidationResult:
        return _result(False, "license_revoked")

    monkeypatch.setattr(module, "validate_license_token", fake_validate_license_token)
    save_license_token("token-123", tmp_path)
    config = AgentConfig(
        license_enforcement_mode=LICENSE_POLICY_HARD_BLOCK,
        license_service_url="https://license.example.com",
        license_issuer="https://license.example.com",
    )
    controller = LicenseEnforcementController(config_dir=tmp_path, config=config)
    with pytest.raises(RuntimeError, match="Hard-block policy prevented startup"):
        await controller.startup_check()


@pytest.mark.asyncio
async def test_controller_runtime_hard_block_calls_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aems_agent import license_enforcement as module

    async def fake_validate_license_token(**_kwargs: object) -> LicenseValidationResult:
        return _result(False, "license_revoked")

    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise RuntimeError(f"exit:{code}")

    monkeypatch.setattr(module, "validate_license_token", fake_validate_license_token)
    monkeypatch.setattr(module.os, "_exit", fake_exit)
    save_license_token("token-123", tmp_path)
    config = AgentConfig(
        license_enforcement_mode=LICENSE_POLICY_HARD_BLOCK,
        license_service_url="https://license.example.com",
        license_issuer="https://license.example.com",
    )
    controller = LicenseEnforcementController(config_dir=tmp_path, config=config)
    with pytest.raises(RuntimeError, match="exit:2"):
        await controller.run_runtime_check_once()
    assert exit_calls == [2]

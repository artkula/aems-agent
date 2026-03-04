"""Tests for CLI behavior around runtime license enforcement."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aems_agent import cli as cli_module
from aems_agent.config import load_config


def test_run_hard_block_exits_nonzero_when_license_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--license-policy", "hard-block"])
    assert result.exit_code == 2
    assert "License hard-block" in result.output


def test_token_command_displays_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    from aems_agent.config import ensure_auth_token

    token = ensure_auth_token(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["token"])
    assert result.exit_code == 0
    assert token in result.output


def test_set_path_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    storage = tmp_path / "exam_storage"
    storage.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["set-path", str(storage)])
    assert result.exit_code == 0
    config = load_config(tmp_path)
    assert config.storage_path == str(storage.resolve())


def test_config_dir_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["config-dir"])
    assert result.exit_code == 0
    assert str(tmp_path) in result.output


def test_license_store_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["license-store", "test-jwt-token"])
    assert result.exit_code == 0
    assert "saved" in result.output.lower()
    from aems_agent.config import load_license_token

    assert load_license_token(tmp_path) == "test-jwt-token"


def test_license_check_no_token_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["license-check"])
    assert result.exit_code != 0


def test_run_soft_block_persists_policy_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_config_dir", lambda: tmp_path)

    captured: dict[str, object] = {}

    def fake_uvicorn_run(app, host: str, port: int, log_level: str) -> None:  # type: ignore[no-untyped-def]
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_uvicorn_run))
    monkeypatch.setattr("aems_agent.app.create_app", lambda *args, **kwargs: object())

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--license-policy",
            "soft-block",
            "--license-check-interval",
            "120",
        ],
    )
    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 61234

    config = load_config(tmp_path)
    assert config.license_enforcement_mode == "soft-block"
    assert config.license_check_interval_seconds == 120

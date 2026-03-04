"""
CLI entry point for the AEMS Local Bridge Agent.

Commands:
    aems-agent run [--port 61234] [--host 127.0.0.1] [--tray]  - Start the agent
    aems-agent token                                              - Display auth token
    aems-agent set-path <path>                                   - Set storage path
    aems-agent config-dir                                        - Show config directory
    aems-agent license-store <jwt>                               - Store license token
    aems-agent license-check                                     - Validate token + heartbeat
"""

import os
import signal
import sys
import json
import asyncio
from pathlib import Path
from typing import Any, Optional

import typer
from fastapi import FastAPI

from .config import (
    AGENT_VERSION,
    AgentConfig,
    ensure_auth_token,
    get_auth_token,
    get_config_dir,
    load_license_token,
    load_config,
    save_license_token,
    save_config,
)
from .license_enforcement import LicenseEnforcementController, VALID_LICENSE_POLICIES
from .license_validation import validate_license_token_sync

app = typer.Typer(
    name="aems-agent",
    help="AEMS Local Bridge Agent - local filesystem access for exam PDFs",
)


def _setup_signal_handlers() -> None:
    """Register signal handlers for graceful shutdown."""

    def _handle_signal(signum: int, frame: object) -> None:
        typer.echo(f"\nReceived signal {signum}, shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    # SIGTERM is not available on Windows
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)


@app.command()
def run(
    port: int = typer.Option(61234, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind to"),
    tray: bool = typer.Option(False, "--tray", help="Show system tray icon"),
    license_policy: Optional[str] = typer.Option(
        None,
        "--license-policy",
        help="License policy: warn, soft-block, or hard-block",
    ),
    license_check_interval: Optional[int] = typer.Option(
        None,
        "--license-check-interval",
        min=60,
        max=86400,
        help="Runtime license check interval in seconds",
    ),
) -> None:
    """Start the AEMS Local Bridge Agent."""
    try:
        import uvicorn  # type: ignore
    except ImportError:
        typer.echo(
            "Error: uvicorn not installed. Run: pip install aems-agent",
            err=True,
        )
        raise typer.Exit(1)

    _setup_signal_handlers()

    config_dir = get_config_dir()
    config = load_config(config_dir)

    config_values = config.model_dump()
    config_values["port"] = port
    config_values["host"] = host
    if license_policy is not None:
        normalized_policy = license_policy.strip().lower()
        if normalized_policy not in VALID_LICENSE_POLICIES:
            allowed = ", ".join(sorted(VALID_LICENSE_POLICIES))
            typer.echo(f"Error: Invalid --license-policy. Expected one of: {allowed}", err=True)
            raise typer.Exit(1)
        config_values["license_enforcement_mode"] = normalized_policy
    if license_check_interval is not None:
        config_values["license_check_interval_seconds"] = int(license_check_interval)

    config = AgentConfig(**config_values)
    save_config(config, config_dir)

    ensure_auth_token(config_dir)

    controller = LicenseEnforcementController(
        config_dir=config_dir,
        config=config,
    )
    try:
        asyncio.run(controller.startup_check())
    except RuntimeError as exc:
        typer.echo(f"License hard-block: {exc}", err=True)
        raise typer.Exit(2)

    typer.echo(f"AEMS Local Bridge Agent v{AGENT_VERSION}")
    typer.echo(f"  Config dir:   {config_dir}")
    typer.echo(f"  Storage path: {config.storage_path or '(not configured)'}")
    typer.echo(f"  Listening on: http://{host}:{port}")
    typer.echo(f"  Token file:   {config_dir / 'auth_token'}")
    typer.echo(f"  License mode: {config.license_enforcement_mode}")
    typer.echo(f"  License check interval: {config.license_check_interval_seconds}s")
    typer.echo("")

    from .app import create_app

    agent_app = create_app(config_dir, skip_startup_license_check=True)

    # Start system tray in a separate thread if requested
    if tray:
        _start_tray(config_dir, agent_app)

    uvicorn.run(agent_app, host=host, port=port, log_level="info")


def _start_tray(config_dir: Path, agent_app: Optional[FastAPI] = None) -> None:
    """Start the system tray icon in a background thread."""
    try:
        from .tray import create_tray

        import threading

        icon: Any = create_tray(config_dir)

        # Wire PIN notifier into FastAPI app state if available
        notifier = getattr(icon, "_aems_pin_notifier", None)
        if notifier is not None and agent_app is not None:
            agent_app.state.tray_notifier = notifier

        thread = threading.Thread(target=icon.run, daemon=True, name="aems-tray")
        thread.start()
        typer.echo("  System tray: enabled")
    except ImportError:
        typer.echo(
            "  System tray: unavailable (install pystray: pip install pystray pillow)",
            err=True,
        )
    except Exception as e:
        typer.echo(f"  System tray: failed to start ({e})", err=True)


@app.command()
def token() -> None:
    """Display the current authentication token."""
    config_dir = get_config_dir()
    existing_token = get_auth_token(config_dir)

    if existing_token:
        typer.echo(existing_token)
    else:
        new_token = ensure_auth_token(config_dir)
        typer.echo(f"Generated new token: {new_token}")


@app.command("set-path")
def set_path(
    path: str = typer.Argument(..., help="Absolute path to storage directory"),
) -> None:
    """Set the local storage directory path."""
    target = Path(path)

    if not target.is_absolute():
        typer.echo(f"Error: Path must be absolute: {path}", err=True)
        raise typer.Exit(1)

    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            typer.echo(f"Created directory: {target}")
        except OSError as e:
            typer.echo(f"Error: Cannot create directory: {e}", err=True)
            raise typer.Exit(1)

    if not target.is_dir():
        typer.echo(f"Error: Not a directory: {target}", err=True)
        raise typer.Exit(1)

    config_dir = get_config_dir()
    config = load_config(config_dir)
    config.storage_path = str(target.resolve())
    save_config(config, config_dir)

    typer.echo(f"Storage path set to: {target.resolve()}")


@app.command("config-dir")
def config_dir() -> None:
    """Show the configuration directory path."""
    typer.echo(str(get_config_dir()))


@app.command("license-store")
def license_store(
    token: Optional[str] = typer.Argument(
        None,
        help=(
            "License JWT token. Prefer passing via AEMS_LICENSE_TOKEN environment variable "
            "to avoid exposing the token in process listings."
        ),
    ),
) -> None:
    """Store a license JWT in the agent config directory."""
    resolved_token = token or os.environ.get("AEMS_LICENSE_TOKEN")
    if not resolved_token:
        typer.echo(
            "Error: No license token provided. Pass it as an argument or set "
            "the AEMS_LICENSE_TOKEN environment variable.",
            err=True,
        )
        raise typer.Exit(1)
    config_dir_path = get_config_dir()
    token_path = save_license_token(resolved_token, config_dir_path)
    typer.echo(f"License token saved: {token_path}")


@app.command("license-check")
def license_check(
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="License JWT token. Defaults to stored token in config dir.",
    ),
    license_url: Optional[str] = typer.Option(
        None,
        "--license-url",
        help="License service base URL. Defaults to config value.",
    ),
    issuer: Optional[str] = typer.Option(
        None,
        "--issuer",
        help="Expected JWT issuer. Defaults to config value.",
    ),
    audience: Optional[str] = typer.Option(
        None,
        "--audience",
        help="Expected JWT audience. Defaults to config value.",
    ),
) -> None:
    """Validate stored license token and perform heartbeat check if required."""
    config_dir_path = get_config_dir()
    config = load_config(config_dir_path)
    resolved_token = token or load_license_token(config_dir_path)
    if not resolved_token:
        typer.echo("No license token provided and no stored token found", err=True)
        raise typer.Exit(1)

    resolved_license_url = license_url or config.license_service_url
    resolved_issuer = issuer or config.license_issuer
    resolved_audience = audience or config.license_audience

    if not resolved_license_url:
        typer.echo("license-url is required (flag or config)", err=True)
        raise typer.Exit(1)
    if not resolved_issuer:
        typer.echo("issuer is required (flag or config)", err=True)
        raise typer.Exit(1)

    try:
        result = validate_license_token_sync(
            token=resolved_token,
            license_service_url=resolved_license_url,
            issuer=resolved_issuer,
            audience=resolved_audience,
            jwks_cache_dir=config_dir_path,
        )
    except Exception as exc:
        typer.echo(f"License validation failed: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(
        json.dumps(
            {
                "valid": result.valid,
                "reason": result.reason,
                "jti": result.jti,
                "heartbeat_checked": result.heartbeat_checked,
                "heartbeat_ok": result.heartbeat_ok,
                "revoked": result.revoked,
                "tier": result.tier,
                "seats": result.seats,
                "expires_at": result.expires_at,
            },
            indent=2,
        )
    )
    if not result.valid:
        raise typer.Exit(2)


def main() -> None:
    """Main entry point for the aems-agent CLI."""
    app()


if __name__ == "__main__":
    main()

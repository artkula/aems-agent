"""
FastAPI application assembly for the AEMS Local Bridge Agent.

Creates and configures the FastAPI app with:
- CORS middleware for browser access
- Bearer token authentication
- Router from routes.py
- Global error handler with structured JSON responses
- Rotating log file
- Startup validation of storage path
"""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import AGENT_VERSION, ensure_auth_token, get_config_dir, load_config
from .routes import router, set_agent_globals

logger = logging.getLogger(__name__)


def _setup_logging(config_dir: Path) -> None:
    """Configure rotating file logging for the agent."""
    log_file = config_dir / "agent.log"
    config_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root_logger = logging.getLogger("aems_agent")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def _validate_storage(config_dir: Path) -> None:
    """Validate that the storage path exists and is writable (if configured)."""
    config = load_config(config_dir)
    if not config.storage_path:
        logger.warning("Storage path not configured. Set it via CLI or Settings page.")
        return

    path = Path(config.storage_path)
    if not path.exists():
        logger.warning("Storage path does not exist: %s", path)
        return

    if not os.access(path, os.W_OK):
        logger.warning("Storage path is not writable: %s", path)


def create_app(config_dir: Optional[Path] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        config_dir: Override config directory (for testing).

    Returns:
        Configured FastAPI app instance.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    config = load_config(config_dir)
    auth_token = ensure_auth_token(config_dir)

    # Set up file logging
    _setup_logging(config_dir)

    # Validate storage on startup
    _validate_storage(config_dir)

    # Set module-level globals for route handlers
    set_agent_globals(config_dir, auth_token)

    # Merge paired origins into allowed origins for CORS.
    # Also allow localhost on any port for developer/self-hosted setups where
    # the web app may run on ports other than 8080.
    all_origins = sorted(set(config.allowed_origins + config.paired_origins))
    localhost_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    app = FastAPI(
        title="AEMS Local Bridge Agent",
        description="Local filesystem access for AEMS exam PDFs",
        version=AGENT_VERSION,
    )

    # Global exception handler for structured JSON errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error_type": type(exc).__name__,
            },
        )

    # CORS middleware for browser access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=all_origins,
        allow_origin_regex=localhost_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST", "DELETE", "HEAD", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-SHA256"],
        expose_headers=["X-SHA256"],
    )

    app.include_router(router)

    return app

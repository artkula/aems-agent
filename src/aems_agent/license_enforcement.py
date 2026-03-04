"""Runtime license enforcement policy for `aems-agent run`."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig, load_license_token
from .license_validation import (
    LicenseValidationResult,
    invalid_license_result,
    validate_license_token,
)

logger = logging.getLogger(__name__)

LICENSE_POLICY_WARN = "warn"
LICENSE_POLICY_SOFT_BLOCK = "soft-block"
LICENSE_POLICY_HARD_BLOCK = "hard-block"
VALID_LICENSE_POLICIES = {
    LICENSE_POLICY_WARN,
    LICENSE_POLICY_SOFT_BLOCK,
    LICENSE_POLICY_HARD_BLOCK,
}


@dataclass(frozen=True)
class LicenseRuntimeSnapshot:
    """Serializable runtime view of license enforcement status."""

    policy_mode: str
    limited_mode_active: bool
    last_valid: bool | None
    last_reason: str
    last_checked_at_utc: str | None


@dataclass(frozen=True)
class LicensePolicyDecision:
    """Result of applying a policy to one validation result."""

    allow_startup: bool
    limited_mode_active: bool
    should_exit_hard_block: bool
    message: str


def evaluate_license_policy(
    *,
    policy_mode: str,
    validation_result: LicenseValidationResult,
) -> LicensePolicyDecision:
    """Evaluate one validation result against a selected policy mode."""
    if policy_mode not in VALID_LICENSE_POLICIES:
        return LicensePolicyDecision(
            allow_startup=False,
            limited_mode_active=False,
            should_exit_hard_block=False,
            message=f"unknown_policy_mode:{policy_mode}",
        )

    if validation_result.valid:
        return LicensePolicyDecision(
            allow_startup=True,
            limited_mode_active=False,
            should_exit_hard_block=False,
            message="license_valid",
        )

    if policy_mode == LICENSE_POLICY_WARN:
        return LicensePolicyDecision(
            allow_startup=True,
            limited_mode_active=False,
            should_exit_hard_block=False,
            message=f"warn:{validation_result.reason}",
        )
    if policy_mode == LICENSE_POLICY_SOFT_BLOCK:
        return LicensePolicyDecision(
            allow_startup=True,
            limited_mode_active=True,
            should_exit_hard_block=False,
            message=f"soft_block:{validation_result.reason}",
        )
    return LicensePolicyDecision(
        allow_startup=False,
        limited_mode_active=True,
        should_exit_hard_block=True,
        message=f"hard_block:{validation_result.reason}",
    )


class LicenseEnforcementController:
    """Manage startup/runtime license policy checks."""

    def __init__(
        self,
        *,
        config_dir: Path,
        config: AgentConfig,
        hard_block_exit_code: int = 2,
    ) -> None:
        self._config_dir = config_dir
        self._config = config
        self._hard_block_exit_code = hard_block_exit_code
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._snapshot = LicenseRuntimeSnapshot(
            policy_mode=config.license_enforcement_mode,
            limited_mode_active=False,
            last_valid=None,
            last_reason="",
            last_checked_at_utc=None,
        )

    @property
    def policy_mode(self) -> str:
        return self._config.license_enforcement_mode

    async def startup_check(self) -> None:
        """Run startup validation and enforce startup policy."""
        result = await self._validate_current_license()
        decision = evaluate_license_policy(
            policy_mode=self.policy_mode,
            validation_result=result,
        )
        async with self._lock:
            self._apply_locked(result, decision)

        if decision.should_exit_hard_block:
            raise RuntimeError(
                f"Hard-block policy prevented startup: {result.reason}"
            )
        if not result.valid:
            logger.warning(
                "License check during startup invalid policy=%s reason=%s",
                self.policy_mode,
                result.reason,
            )

    async def start_runtime_monitor(self) -> None:
        """Start background periodic validation monitor."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._runtime_monitor_loop())

    async def stop_runtime_monitor(self) -> None:
        """Stop background monitor task."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def snapshot(self) -> LicenseRuntimeSnapshot:
        """Return immutable snapshot of current enforcement state."""
        return self._snapshot

    def _force_limited_mode(self, active: bool) -> None:
        """Test helper: force limited_mode_active flag on the snapshot."""
        self._snapshot = LicenseRuntimeSnapshot(
            policy_mode=self._snapshot.policy_mode,
            limited_mode_active=active,
            last_valid=self._snapshot.last_valid,
            last_reason=self._snapshot.last_reason,
            last_checked_at_utc=self._snapshot.last_checked_at_utc,
        )

    def is_write_permitted(self, *, method: str, path: str) -> bool:
        """Return whether write operation is permitted under current policy state."""
        if not self._snapshot.limited_mode_active:
            return True

        method_upper = method.upper()
        if method_upper not in {"PUT", "PATCH", "POST", "DELETE"}:
            return True

        # Limited mode explicitly blocks local file mutation and storage-path changes.
        if path == "/config/path" and method_upper == "PUT":
            return False
        if path.startswith("/files/"):
            return False
        return True

    async def _runtime_monitor_loop(self) -> None:
        interval = max(int(self._config.license_check_interval_seconds), 60)
        while True:
            # Sleep first: create_app already runs an initial check at startup.
            await asyncio.sleep(interval)
            await self.run_runtime_check_once()

    async def run_runtime_check_once(self) -> None:
        """Execute one runtime validation check and apply policy action."""
        result = await self._validate_current_license()
        decision = evaluate_license_policy(
            policy_mode=self.policy_mode,
            validation_result=result,
        )
        async with self._lock:
            self._apply_locked(result, decision)

        if result.valid:
            return

        logger.warning(
            "License check during runtime invalid policy=%s reason=%s",
            self.policy_mode,
            result.reason,
        )
        if decision.should_exit_hard_block:
            logger.error(
                "Hard-block policy triggered runtime termination reason=%s",
                result.reason,
            )
            os._exit(self._hard_block_exit_code)

    def _apply_locked(
        self,
        result: LicenseValidationResult,
        decision: LicensePolicyDecision,
    ) -> None:
        self._snapshot = LicenseRuntimeSnapshot(
            policy_mode=self.policy_mode,
            limited_mode_active=bool(decision.limited_mode_active),
            last_valid=result.valid,
            last_reason=result.reason,
            last_checked_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    async def _validate_current_license(self) -> LicenseValidationResult:
        token = load_license_token(self._config_dir)
        if not token:
            return invalid_license_result("missing_license_token")
        if not self._config.license_service_url.strip():
            return invalid_license_result("missing_license_service_url")
        if not self._config.license_issuer.strip():
            return invalid_license_result("missing_license_issuer")

        return await validate_license_token(
            token=token,
            license_service_url=self._config.license_service_url,
            issuer=self._config.license_issuer,
            audience=self._config.license_audience,
            jwks_cache_dir=self._config_dir,
        )

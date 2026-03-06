"""Stable device identifier for license heartbeat."""
from __future__ import annotations

import hashlib
import platform
import uuid


def get_device_id() -> str:
    """Return a stable SHA-256 hash identifying this machine.

    Uses platform node (MAC address or hostname fallback) combined
    with OS platform string. Not cryptographically unforgeable ---
    this is a deterrent, not a guarantee.
    """
    node = str(uuid.getnode())
    system = platform.system()
    raw = f"{node}:{system}"
    return hashlib.sha256(raw.encode()).hexdigest()

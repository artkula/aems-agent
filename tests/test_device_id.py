"""Tests for stable device ID generation."""
from aems_agent.device_id import get_device_id


def test_device_id_is_hex_string() -> None:
    """Device ID is a hex-encoded hash."""
    did = get_device_id()
    assert isinstance(did, str)
    assert len(did) == 64  # SHA-256 hex
    int(did, 16)  # valid hex


def test_device_id_is_stable() -> None:
    """Same machine returns same ID across calls."""
    assert get_device_id() == get_device_id()

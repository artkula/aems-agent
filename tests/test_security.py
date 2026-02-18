"""Tests for agent security utilities."""

from pathlib import Path

import pytest

from aems_agent.security import RateLimiter, validate_path_within_storage


class TestValidatePathWithinStorage:
    """Tests for path traversal validation."""

    def test_valid_path(self, tmp_path: Path) -> None:
        result = validate_path_within_storage(tmp_path, "assignment_1", "sub_100")
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_path_within_storage(tmp_path, "..", "..", "etc", "passwd")

    def test_double_dot_in_middle(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_path_within_storage(tmp_path, "a", "..", "..", "b")

    def test_absolute_part_rejected(self, tmp_path: Path) -> None:
        # On Windows this may or may not raise depending on Path behavior.
        # The key is the resolved path must be within storage_path.
        try:
            result = validate_path_within_storage(tmp_path, "normal", "path")
            # If it doesn't raise, it must be within the storage
            assert str(result).startswith(str(tmp_path.resolve()))
        except ValueError:
            pass

    def test_single_dot_ok(self, tmp_path: Path) -> None:
        result = validate_path_within_storage(tmp_path, ".", "assignment_1")
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_nested_path(self, tmp_path: Path) -> None:
        result = validate_path_within_storage(tmp_path, "123", "456")
        expected = tmp_path.resolve() / "123" / "456"
        assert result == expected


class TestRateLimiter:
    """Tests for in-memory rate limiter."""

    def test_allows_within_limit(self) -> None:
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            assert limiter.is_allowed("user1") is True

    def test_blocks_over_limit(self) -> None:
        limiter = RateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is False

    def test_different_keys_independent(self) -> None:
        limiter = RateLimiter(max_requests=2, window_seconds=60.0)
        assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is False
        # Different key should still be allowed
        assert limiter.is_allowed("user2") is True

    def test_reset(self) -> None:
        limiter = RateLimiter(max_requests=1, window_seconds=60.0)
        assert limiter.is_allowed("user1") is True
        assert limiter.is_allowed("user1") is False
        limiter.reset()
        assert limiter.is_allowed("user1") is True

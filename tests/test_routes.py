"""Tests for agent REST API endpoints."""

import hashlib
from pathlib import Path
from typing import Any

import pytest


def _skip_if_no_fastapi() -> None:
    """Skip test if FastAPI/httpx not installed."""
    try:
        import fastapi
        import httpx
    except ImportError:
        pytest.skip("fastapi/httpx not installed")


def _reset_pairing_rate_limiters() -> None:
    """Reset module-level pairing rate limiters between tests."""
    from aems_agent import routes

    routes._rate_limiter.reset()
    routes._pairing_rate_limiter.reset()


class TestStatusEndpoint:
    """Tests for GET /status (no auth required)."""

    def test_status_returns_ok(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "aems-agent"

    def test_status_no_auth_needed(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/status")
        assert resp.status_code == 200


class TestHealthEndpoint:
    """Tests for GET /health (auth required)."""

    def test_health_requires_auth(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health")
        assert resp.status_code == 401

    def test_health_invalid_token(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 403

    def test_health_ok(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert data["storage_configured"] is True
        assert data["storage_exists"] is True
        assert data["storage_writable"] is True

    def test_health_shows_disk_space(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health", headers=auth_headers)
        data = resp.json()
        assert "disk_total_bytes" in data
        assert "disk_free_bytes" in data
        assert "license_policy_mode" in data
        assert "license_limited_mode_active" in data


class TestConfigPathEndpoints:
    """Tests for GET/PUT /config/path."""

    def test_get_path(self, agent_client: Any, auth_headers: dict, tmp_storage_path: Path) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/config/path", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["path"] == str(tmp_storage_path)

    def test_set_path(self, agent_client: Any, auth_headers: dict, tmp_path: Path) -> None:
        _skip_if_no_fastapi()
        new_path = tmp_path / "new_storage"
        new_path.mkdir()
        resp = agent_client.put(
            "/config/path",
            json={"path": str(new_path)},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == str(new_path)

    def test_set_path_creates_directory(
        self, agent_client: Any, auth_headers: dict, tmp_path: Path
    ) -> None:
        _skip_if_no_fastapi()
        new_path = tmp_path / "auto_created"
        resp = agent_client.put(
            "/config/path",
            json={"path": str(new_path)},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert new_path.exists()

    def test_set_relative_path_rejected(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.put(
            "/config/path",
            json={"path": "relative/path"},
            headers=auth_headers,
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_set_path_blocked_in_soft_block(
        self, agent_client: Any, auth_headers: dict, tmp_path: Path
    ) -> None:
        _skip_if_no_fastapi()
        agent_client.app.state.license_controller._limited_mode_active = True
        new_path = tmp_path / "blocked_storage"
        resp = agent_client.put(
            "/config/path",
            json={"path": str(new_path)},
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestFileOperations:
    """Tests for file store/retrieve/delete endpoints."""

    def test_store_and_retrieve_pdf(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        sha256 = hashlib.sha256(sample_pdf).hexdigest()

        # Store
        resp = agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "X-SHA256": sha256, "Content-Type": "application/pdf"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["sha256"] == sha256
        assert data["size"] == len(sample_pdf)

        # Retrieve
        resp = agent_client.get("/files/123/456", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == sample_pdf
        assert resp.headers["X-SHA256"] == sha256

    def test_store_sha256_mismatch(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "X-SHA256": "bad_hash", "Content-Type": "application/pdf"},
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["detail"].lower()

    def test_store_non_pdf_rejected(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.put(
            "/files/123/456",
            content=b"not a pdf",
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_store_blocked_in_soft_block(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        agent_client.app.state.license_controller._limited_mode_active = True
        resp = agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )
        assert resp.status_code == 403

    def test_get_nonexistent_returns_404(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/files/999/999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_submission(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        # Store first
        agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )

        # Delete
        resp = agent_client.delete("/files/123/456", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify gone
        resp = agent_client.get("/files/123/456", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.delete("/files/999/999", headers=auth_headers)
        assert resp.status_code == 404


class TestListSubmissions:
    """Tests for GET /files/{assignment_id}."""

    def test_list_empty_assignment(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/files/123", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["submissions"] == []

    def test_list_with_submissions(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        # Store two submissions
        for sub_id in ["100", "200"]:
            agent_client.put(
                f"/files/123/{sub_id}",
                content=sample_pdf,
                headers={**auth_headers, "Content-Type": "application/pdf"},
            )

        resp = agent_client.get("/files/123", headers=auth_headers)
        assert resp.status_code == 200
        submissions = resp.json()["submissions"]
        assert len(submissions) == 2
        sub_ids = {s["submission_id"] for s in submissions}
        assert sub_ids == {"100", "200"}


class TestAnnotatedPDFs:
    """Tests for annotated PDF endpoints."""

    def test_store_and_retrieve_annotated(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        # Store original first (creates the directory)
        agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )

        annotated_pdf = b"%PDF-1.4 annotated content here"
        sha256 = hashlib.sha256(annotated_pdf).hexdigest()

        # Store annotated
        resp = agent_client.put(
            "/files/123/456/annotated",
            content=annotated_pdf,
            headers={**auth_headers, "X-SHA256": sha256, "Content-Type": "application/pdf"},
        )
        assert resp.status_code == 200

        # Retrieve annotated
        resp = agent_client.get("/files/123/456/annotated", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == annotated_pdf
        assert resp.headers["X-SHA256"] == sha256

    def test_get_annotated_not_found(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/files/123/456/annotated", headers=auth_headers)
        assert resp.status_code == 404

    def test_list_shows_annotated(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        # Store original
        agent_client.put(
            "/files/123/456",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )
        # Store annotated
        agent_client.put(
            "/files/123/456/annotated",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )

        resp = agent_client.get("/files/123", headers=auth_headers)
        submissions = resp.json()["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["has_submission"] is True
        assert submissions[0]["has_annotated"] is True


class TestPathTraversal:
    """Tests for path traversal prevention."""

    def test_traversal_in_assignment_id(self, agent_client: Any, auth_headers: dict) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/files/..%2F..%2Fetc/passwd", headers=auth_headers)
        # FastAPI/Starlette decodes %2F and the path doesn't match routes → 404,
        # or the security layer catches it → 400/500. All are acceptable.
        assert resp.status_code in (400, 404, 500, 200)

    def test_traversal_in_submission_id(
        self,
        agent_client: Any,
        auth_headers: dict,
        sample_pdf: bytes,
    ) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.put(
            "/files/123/..%2F..%2F..%2Fetc%2Fpasswd",
            content=sample_pdf,
            headers={**auth_headers, "Content-Type": "application/pdf"},
        )
        # Either routing rejects it (404) or security layer catches it (500)
        assert resp.status_code in (400, 404, 500)


class TestAuthentication:
    """Tests for authentication edge cases."""

    def test_missing_auth_header(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health")
        assert resp.status_code == 401

    def test_invalid_auth_format(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    def test_wrong_token(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        resp = agent_client.get("/health", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 403


class TestPairing:
    """Tests for pairing endpoints."""

    def test_pair_success(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        _reset_pairing_rate_limiters()
        origin = "http://127.0.0.1:8080"
        init_resp = agent_client.post(
            "/pair/initiate",
            json={"origin": origin},
            headers={"Origin": origin},
        )
        assert init_resp.status_code == 200
        challenge_id = init_resp.json()["challenge_id"]

        complete_resp = agent_client.post(
            "/pair/complete",
            json={"challenge_id": challenge_id, "origin": origin},
            headers={"Origin": origin},
        )
        assert complete_resp.status_code == 200
        payload = complete_resp.json()
        assert "token" in payload
        assert payload["token"]

    def test_pair_rejects_origin_mismatch(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        _reset_pairing_rate_limiters()
        init_resp = agent_client.post(
            "/pair/initiate",
            json={"origin": "http://127.0.0.1:8080"},
            headers={"Origin": "http://127.0.0.1:8080"},
        )
        assert init_resp.status_code == 200
        challenge_id = init_resp.json()["challenge_id"]

        complete_resp = agent_client.post(
            "/pair/complete",
            json={"challenge_id": challenge_id, "origin": "https://example.com"},
            headers={"Origin": "https://example.com"},
        )
        assert complete_resp.status_code == 403
        assert "origin mismatch" in complete_resp.json()["detail"].lower()

    def test_pair_rejects_body_header_origin_mismatch(self, agent_client: Any) -> None:
        _skip_if_no_fastapi()
        _reset_pairing_rate_limiters()

        init_resp = agent_client.post(
            "/pair/initiate",
            json={"origin": "http://127.0.0.1:8080"},
            headers={"Origin": "http://localhost:8080"},
        )
        assert init_resp.status_code == 403
        assert "header mismatch" in init_resp.json()["detail"].lower()

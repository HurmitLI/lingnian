from __future__ import annotations

import re

from app.core.config import get_settings


def test_request_id_is_returned_and_shared_with_errors(client):
    request_id = "browser-request-2026"
    response = client.post(
        "/api/v1/families",
        headers={"X-Request-ID": request_id},
        json={},
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["error"]["request_id"] == request_id


def test_unsafe_request_id_is_replaced(client):
    response = client.get(
        "/api/v1/health",
        headers={"X-Request-ID": "unsafe id with spaces"},
    )

    generated = response.headers["X-Request-ID"]
    assert response.status_code == 200
    assert generated != "unsafe id with spaces"
    assert re.fullmatch(r"[0-9a-f-]{36}", generated)


def test_readiness_reports_database_and_storage(client, monkeypatch, tmp_path):
    local = client.get("/api/v1/readiness")
    assert local.status_code == 200
    assert local.json() == {
        "status": "ready",
        "database": "ok",
        "persistent_storage": "not_required",
    }

    settings = get_settings()
    monkeypatch.setattr(settings, "formal_auth_required", True)
    monkeypatch.setattr(
        settings,
        "formal_persistent_storage_mount",
        tmp_path / "missing-storage",
    )
    unavailable = client.get("/api/v1/readiness")
    assert unavailable.status_code == 503
    assert unavailable.json()["persistent_storage"] == "unavailable"

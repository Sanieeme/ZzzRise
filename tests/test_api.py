from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from backend.main import app, as_utc

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_as_utc_handles_sqlite_naive_datetime():
    naive = datetime(2026, 11, 19, 20, 0, 0)
    aware = datetime(2026, 11, 19, 22, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    assert as_utc(naive) == datetime(2026, 11, 19, 20, 0, 0, tzinfo=timezone.utc)
    assert as_utc(aware) == datetime(2026, 11, 19, 20, 0, 0, tzinfo=timezone.utc)


def test_end_time_comparison_accepts_mixed_datetime_formats():
    started_at = datetime(2026, 11, 19, 20, 0, 0)  # SQLite's naive value
    ended_at = datetime(2026, 11, 19, 22, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    assert as_utc(ended_at) >= as_utc(started_at)


def test_exact_wake_time_session_can_last_up_to_24_hours():
    from uuid import uuid4

    email = f"wake-time-{uuid4().hex[:10]}@example.com"
    registered = client.post("/api/auth/register", json={
        "name": "Wake Time Tester", "email": email, "password": "secure-pass-123"
    })
    assert registered.status_code == 201, registered.text

    allowed = client.post("/api/sessions", json={
        "started_at": "2026-11-19T12:00:00+00:00", "target_minutes": 1440, "start_method": "wake_time"
    })
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["target_minutes"] == 1440


def test_cross_origin_api_mutation_is_rejected():
    response = client.post(
        "/api/auth/login",
        headers={"Origin": "https://untrusted.example"},
        json={"email": "someone@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 403


def test_security_headers_are_present():
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"

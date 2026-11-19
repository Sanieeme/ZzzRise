from uuid import uuid4
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_register_login_and_private_sessions():
    email = f"zzrise-{uuid4().hex[:10]}@example.com"
    registered = client.post("/api/auth/register", json={
        "name": "Test Sleeper", "email": email, "password": "strong-pass-123"
    })
    assert registered.status_code == 201, registered.text
    assert registered.json()["email"] == email
    assert client.get("/api/sessions").status_code == 200

    created = client.post("/api/sessions", json={
        "started_at": "2026-11-19T12:00:00+00:00", "target_minutes": 60
    })
    assert created.status_code == 201, created.text
    assert created.json()["target_minutes"] == 60

    client.post("/api/auth/logout")
    assert client.get("/api/sessions").status_code == 401

    other_email = f"other-{uuid4().hex[:10]}@example.com"
    other_user = client.post("/api/auth/register", json={
        "name": "Another Sleeper", "email": other_email, "password": "strong-pass-456"
    })
    assert other_user.status_code == 201
    assert client.get("/api/sessions").json() == []
    client.post("/api/auth/logout")

    logged_in = client.post("/api/auth/login", json={"email": email, "password": "strong-pass-123"})
    assert logged_in.status_code == 200, logged_in.text
    rows = client.get("/api/sessions").json()
    assert any(row["target_minutes"] == 60 for row in rows)


def test_invalid_login_is_rejected():
    response = client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong-pass-123"})
    assert response.status_code == 401

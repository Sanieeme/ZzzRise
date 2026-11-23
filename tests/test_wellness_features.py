from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def register_user():
    email = f"wellness-{uuid4().hex[:10]}@example.com"
    response = client.post("/api/auth/register", json={
        "name": "Wellness Tester", "email": email, "password": "safe-password-123"
    })
    assert response.status_code == 201, response.text
    return email


def test_journal_is_private_to_signed_in_user():
    first_email = register_user()
    saved = client.post("/api/journal", json={
        "entry_date": "2026-11-20", "mood": "Rested", "notes": "Woke up feeling refreshed."
    })
    assert saved.status_code == 201, saved.text
    assert len(client.get("/api/journal").json()) == 1

    client.post("/api/auth/logout")
    register_user()
    assert client.get("/api/journal").json() == []
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": first_email, "password": "safe-password-123"}).status_code == 200
    assert len(client.get("/api/journal").json()) == 1


def test_analytics_returns_empty_state_for_new_user():
    register_user()
    response = client.get("/api/analytics")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sleep_score"] is None
    assert data["streak_days"] == 0
    assert data["completed_sessions"] == 0
    client.post("/api/auth/logout")


def test_ai_coach_uses_local_model_and_only_signed_in_user_context(monkeypatch):
    import backend.main as main_module

    register_user()
    async def fake_local_model(question, summary, records, journal):
        assert question == "How did I sleep this week?"
        assert summary["records_available"] == 0
        assert records == []
        assert journal == []
        return "You have not recorded enough sleep sessions yet."

    monkeypatch.setattr(main_module, "ask_local_model", fake_local_model)
    response = client.post("/api/ai/coach", json={"question": "How did I sleep this week?"})
    assert response.status_code == 200, response.text
    assert response.json()["ai_mode"] == "local"
    assert "not recorded enough" in response.json()["answer"]
    client.post("/api/auth/logout")


def test_local_ai_rejects_remote_host_by_default(monkeypatch):
    from backend.ai.coach import _ollama_url, LocalAIError
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.com:11434")
    monkeypatch.setenv("OLLAMA_ALLOW_REMOTE", "false")
    try:
        _ollama_url()
        assert False, "Remote Ollama host should be blocked by default"
    except LocalAIError as exc:
        assert "this computer" in str(exc)


def test_android_health_connect_import_is_private_and_idempotent():
    email = register_user()
    mobile_login = client.post("/api/mobile/auth/login", json={
        "email": email, "password": "safe-password-123"
    })
    assert mobile_login.status_code == 200, mobile_login.text
    token = mobile_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"records": [{
        "record_id": "health-connect-record-001",
        "start_time": "2026-11-20T22:00:00Z",
        "end_time": "2026-11-21T06:00:00Z",
        "source_app": "com.sec.android.app.shealth"
    }]}
    imported = client.post("/api/health-connect/sync", json=payload, headers=headers)
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    duplicate = client.post("/api/health-connect/sync", json=payload, headers=headers)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["imported"] == 0
    assert duplicate.json()["skipped"] == 1

    client.post("/api/auth/logout")
    other_email = register_user()
    other_login = client.post("/api/mobile/auth/login", json={
        "email": other_email, "password": "safe-password-123"
    })
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    assert client.get("/api/sessions").json() == []
    assert client.post("/api/health-connect/sync", json=payload, headers=other_headers).json()["imported"] == 1
    client.post("/api/auth/logout")


def test_qr_pairing_is_single_use_and_returns_mobile_access():
    email = register_user()
    created = client.post("/api/mobile/pairing/create")
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["qr_data"].startswith("data:image/png;base64,")
    assert data["expires_in"] == 300
    raw_value = data["scan_value"]
    assert raw_value.startswith("ZZRISEPAIR:")
    pairing_token = raw_value.split(":", 1)[1]

    client.post("/api/auth/logout")
    claimed = client.post("/api/mobile/pairing/claim", json={"pairing_token": pairing_token})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["user"]["email"] == email
    mobile_headers = {"Authorization": f"Bearer {claimed.json()['access_token']}"}
    assert client.get("/api/health-connect/status").status_code == 401
    assert client.post("/api/health-connect/sync", json={"records": []}, headers=mobile_headers).status_code == 200

    reused = client.post("/api/mobile/pairing/claim", json={"pairing_token": pairing_token})
    assert reused.status_code == 400

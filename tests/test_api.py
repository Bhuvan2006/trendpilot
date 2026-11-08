import time

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_ID", "mock")
    with TestClient(main.create_app(tmp_path / "t.db")) as c:
        yield c


def run_to_end(client):
    assert client.post("/api/pipeline/run", json={}).status_code == 202
    for _ in range(200):
        if not client.get("/api/status").json()["running"]:
            return client.get("/api/runs").json()[0]
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_full_flow_over_http(client):
    run = run_to_end(client)
    assert run["status"] == "done"
    assert client.get("/api/trends").json()[0]["score"] > 0
    draft = client.get("/api/content").json()[0]
    assert client.post(f"/api/content/{draft['id']}/approve", json={"reviewer": "bhuvan"}).status_code == 200
    post = client.post(f"/api/publish/{draft['id']}", json={}).json()
    assert post["status"] == "published" and post["mode"] == "dry_run"
    assert client.post(f"/api/publish/{draft['id']}", json={}).json()["external_id"] == post["external_id"]


def test_publish_before_approval_is_refused_and_audited(client):
    run_to_end(client)
    draft = client.get("/api/content").json()[0]
    r = client.post(f"/api/publish/{draft['id']}", json={})
    assert r.status_code == 409 and "approve" in r.json()["detail"]
    assert any(a["action"] == "publish" and a["allowed"] == 0 for a in client.get("/api/audit").json())


def test_reject_feeds_learning_memory(client):
    run_to_end(client)
    draft = client.get("/api/content").json()[0]
    client.post(f"/api/content/{draft['id']}/reject", json={"reviewer": "bhuvan", "comment": "too salesy"})
    assert "too salesy" in client.get("/api/memory").json()["learnings"]


def test_second_run_during_active_run_is_refused(client, monkeypatch):
    monkeypatch.setattr(main.pipeline, "run_pipeline", lambda *a, **k: time.sleep(0.4))
    assert client.post("/api/pipeline/run", json={}).status_code == 202
    assert client.post("/api/pipeline/run", json={}).status_code == 409
    time.sleep(0.5)


def test_non_json_post_is_refused(client):
    # Blocks another website from triggering a paid run or an approval through the browser.
    r = client.post("/api/pipeline/run", content="{}", headers={"content-type": "text/plain"})
    assert r.status_code == 422


def test_missing_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_ID", "claude-sonnet-5-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: None)
    with TestClient(main.create_app(tmp_path / "t.db")) as c:
        r = c.post("/api/pipeline/run", json={})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_openrouter_needs_its_own_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("MODEL_ID", "nvidia/some-model:free")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "set-but-irrelevant")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: None)
    with TestClient(main.create_app(tmp_path / "t.db")) as c:
        assert c.get("/api/status").json()["key_configured"] is False
        r = c.post("/api/pipeline/run", json={})
    assert r.status_code == 400 and "OPENROUTER_API_KEY" in r.json()["detail"]


def test_nvidia_needs_its_own_key_and_has_a_price_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("MODEL_ID", "nvidia/nemotron-3-ultra-550b-a55b")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: None)
    with TestClient(main.create_app(tmp_path / "t.db")) as c:
        r = c.post("/api/pipeline/run", json={})
    assert r.status_code == 400 and "NVIDIA_API_KEY" in r.json()["detail"]
    assert main.is_priced("nvidia/nemotron-3-ultra-550b-a55b")


def test_interrupted_run_is_closed_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    path = tmp_path / "t.db"
    main.pipeline.create_run(path, "mock", "mock")
    with TestClient(main.create_app(path)) as c:
        assert c.get("/api/runs").json()[0]["status"] == "failed"


def test_dashboard_is_served_and_says_dry_run(client):
    r = client.get("/")
    assert r.status_code == 200 and "Dry run" in r.text

"""The dashboard has no login, so it must only take orders from itself.

It used to listen on every interface with CORS open to "*" plus
credentials, so any web page the streamer had open, or anyone on the LAN,
could rewrite settings. The emergency mute endpoints answered 500 (the stub
user had no roles) or "Bot not found" (they searched the task registry).
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.config_unified import Settings, local_origins

EVIL = "https://evil.example"


@pytest.fixture
def client_and_bot():
    from src.api.app import app

    bot = SimpleNamespace(muted=False)
    old = getattr(app.state, "bot", None)
    app.state.bot = bot
    try:
        yield TestClient(app), bot
    finally:
        app.state.bot = old


# ------------------------------------------------------------ defaults

def test_api_listens_on_loopback_by_default(monkeypatch):
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings.from_env().api_host == "127.0.0.1"


def test_cors_defaults_to_the_dashboards_own_origin(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("API_PORT", "8123")
    origins = Settings.from_env().cors_origins
    assert origins == local_origins(8123)
    assert "*" not in origins


def test_cors_origins_can_be_widened_explicitly(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://studio.lan:8000, http://localhost:8000")
    assert Settings.from_env().cors_origins == ["http://studio.lan:8000", "http://localhost:8000"]


# ------------------------------------------------------- cross-origin

def test_a_foreign_page_cannot_unmute_the_bot(client_and_bot):
    client, bot = client_and_bot
    bot.muted = True
    r = client.post("/api/unmute", headers={"Origin": EVIL})
    assert r.status_code == 403
    assert bot.muted is True


def test_a_foreign_page_cannot_write_settings(client_and_bot):
    client, _ = client_and_bot
    r = client.post("/api/v2/settings", json={"key": "x", "value": 1}, headers={"Origin": EVIL})
    assert r.status_code == 403


def test_foreign_preflight_gets_no_cors_grant(client_and_bot):
    client, _ = client_and_bot
    r = client.options(
        "/api/mute",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") is None


def test_reads_from_anywhere_still_pass(client_and_bot):
    client, _ = client_and_bot
    assert client.get("/api/mute/status", headers={"Origin": EVIL}).status_code == 200


# ---------------------------------------------------------- mute works

def test_dashboard_mute_and_unmute_flip_the_real_flag(client_and_bot):
    client, bot = client_and_bot
    own = {"Origin": "http://localhost:8000"}

    r = client.post("/api/mute", headers=own)
    assert r.status_code == 200 and r.json()["muted"] is True
    assert bot.muted is True
    assert client.get("/api/mute/status").json()["muted"] is True

    r = client.post("/api/unmute", headers=own)
    assert r.status_code == 200 and r.json()["muted"] is False
    assert bot.muted is False


def test_settings_endpoint_no_longer_500s_on_the_stub_user(client_and_bot):
    client, _ = client_and_bot
    assert client.get("/api/v2/settings/somebody").status_code == 200


def test_mute_without_a_bot_says_so(client_and_bot):
    from src.api import dependencies

    client, _ = client_and_bot
    from src.api.app import app
    app.state.bot = None
    old = dependencies._bot_instance
    dependencies._bot_instance = None
    try:
        assert client.post("/api/mute").status_code == 503
    finally:
        dependencies._bot_instance = old

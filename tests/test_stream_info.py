"""StreamInfo + Helix helpers: boot seed, EventSub updates, change callback.

Helix `_request` is monkeypatched; no network. Clock injected.
"""
import pytest

from features.stream_info import StreamInfo
from twitch import helix


class FakeHelix:
    """Scripted responses keyed by (method, path)."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __call__(self, method, path, *, client_id, token, params=None, timeout=3.0):
        self.calls.append((method, path, dict(params or {}), token))
        r = self.responses.get((method, path))
        return r() if callable(r) else r


@pytest.fixture
def fake(monkeypatch):
    def _install(responses):
        f = FakeHelix(responses)
        monkeypatch.setattr(helix, "_request", f)
        return f
    return _install


def make(on_change=None, broadcaster_id=None, t=1000.0):
    clock = {"t": t}
    si = StreamInfo("cid", lambda: "tok", "#Cassova_", broadcaster_id=broadcaster_id,
                    on_change=on_change, clock=lambda: clock["t"])
    return si, clock


# ---------------------------------------------------------------- helix

async def test_get_channel_info_maps_fields(fake):
    fake({("GET", "channels"): {"data": [{"game_name": "Elden Ring", "game_id": "1",
                                          "title": "no hit run", "broadcaster_login": "cassova_"}]}})
    info = await helix.get_channel_info("cid", "tok", "123")
    assert info == {"game": "Elden Ring", "game_id": "1", "title": "no hit run", "login": "cassova_"}


async def test_get_channel_info_empty_or_failed(fake):
    fake({("GET", "channels"): {"data": []}})
    assert await helix.get_channel_info("cid", "tok", "123") is None
    fake({("GET", "channels"): None})
    assert await helix.get_channel_info("cid", "tok", "123") is None


async def test_create_clip_builds_public_url(fake):
    f = fake({("POST", "clips"): {"data": [{"id": "AbcXyz", "edit_url": "https://clips.twitch.tv/AbcXyz/edit"}]}})
    clip = await helix.create_clip("cid", "tok", "123")
    assert clip["url"] == "https://clips.twitch.tv/AbcXyz"
    assert clip["id"] == "AbcXyz"
    assert f.calls[0][2] == {"broadcaster_id": "123"}


async def test_create_clip_failure_is_none(fake):
    fake({("POST", "clips"): None})
    assert await helix.create_clip("cid", "tok", "123") is None


async def test_request_refuses_without_credentials():
    assert await helix._request("GET", "channels", client_id="", token="x") is None
    assert await helix._request("GET", "channels", client_id="x", token="") is None


# ----------------------------------------------------------- stream info

async def test_refresh_resolves_id_then_reads_channel(fake):
    f = fake({
        ("GET", "users"): {"data": [{"id": "123"}]},
        ("GET", "channels"): {"data": [{"game_name": "Elden Ring", "title": "hi"}]},
    })
    si, _ = make()
    assert await si.refresh()
    assert si.broadcaster_id == "123"
    assert f.calls[0][2] == {"login": "cassova_"}  # normalized: lowercase, no '#'
    assert si.game == "Elden Ring" and si.title == "hi" and si.source == "helix"
    assert si.describe() == 'playing Elden Ring, stream title "hi"'


async def test_refresh_skips_user_lookup_when_id_known(fake):
    f = fake({("GET", "channels"): {"data": [{"game_name": "Factorio", "title": ""}]}})
    si, _ = make(broadcaster_id="123")
    assert await si.refresh()
    assert [c[1] for c in f.calls] == ["channels"]
    assert si.describe() == "playing Factorio"


async def test_refresh_failure_leaves_state_untouched(fake):
    fake({("GET", "users"): {"data": []}})
    si, _ = make()
    assert not await si.refresh()
    assert si.game is None and si.describe() == ""


async def test_channel_update_event_changes_game_and_fires_callback():
    seen = []
    si, clock = make(on_change=lambda old, new, title: seen.append((old, new, title)))
    await si.handle_channel_update({"category_name": "Elden Ring", "title": "t1"})
    clock["t"] = 2000.0
    await si.handle_channel_update({"category_name": "Elden Ring", "title": "t2"})  # title only
    await si.handle_channel_update({"category_name": "Just Chatting", "title": "t2"})
    assert seen == [(None, "Elden Ring", "t1"), ("Elden Ring", "Just Chatting", "t2")]
    assert si.source == "eventsub" and si.updated_at == 2000.0


async def test_callback_exception_does_not_break_update():
    def boom(*_):
        raise RuntimeError("nope")
    si, _ = make(on_change=boom)
    await si.handle_channel_update({"category_name": "Elden Ring", "title": ""})
    assert si.game == "Elden Ring"


async def test_online_offline_reflected_in_describe():
    si, _ = make()
    await si.handle_channel_update({"category_name": "Elden Ring", "title": ""})
    await si.handle_stream_offline({})
    assert si.is_live is False
    assert "offline" in si.describe()
    await si.handle_stream_online({"started_at": "2026-08-22T20:00:00Z"})
    assert si.is_live is True
    assert "offline" not in si.describe()

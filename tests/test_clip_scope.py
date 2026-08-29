"""The clip feature needs a scope the auth script never requested.

Confirmed live 2026-08-27: "Helix POST clips: 401 token rejected or missing
scope" twice overnight, and "Clip failed (both tokens)". !clip and the
auto-clipper have never once worked, because clips:edit was absent from the
authorization request -- no token minted by this script could ever create a
clip.
"""
import re
from pathlib import Path

SCRIPT = Path("get_twitch_tokens.py").read_text(encoding="utf-8")


def _scopes():
    block = re.search(r"SCOPES = \[(.*?)\]", SCRIPT, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def test_clips_edit_is_requested():
    assert "clips:edit" in _scopes(), "no minted token can create a clip without it"


def test_the_rest_of_the_scopes_survived():
    scopes = _scopes()
    for required in ("chat:read", "chat:edit", "channel:read:ads",
                     "channel:read:subscriptions", "bits:read",
                     "moderator:read:followers"):
        assert required in scopes, required


def test_no_duplicate_scopes():
    scopes = _scopes()
    assert len(scopes) == len(set(scopes)), "Twitch rejects a duplicated scope"


def test_scopes_look_like_real_twitch_scopes():
    for s in _scopes():
        assert re.fullmatch(r"[a-z]+(:[a-z]+)*", s), s

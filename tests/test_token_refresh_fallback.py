"""Losing the token .txt file must not silently end auto-refresh.

`register_account` read the refresh token only from twitch_tokens_<acct>.txt.
That file looks like a disposable dump of credentials -- it is easy to
delete after pasting the access token into .env -- and when it went missing
the account was simply not registered. Nothing failed at startup; the token
just expired about four hours later, mid-stream. Meanwhile .env already
held TWITCH_BROADCASTER_REFRESH_TOKEN, read by nothing.

No test here asserts on a token VALUE; only on which source was used.
"""
from pathlib import Path

import pytest

from twitch.token_refresher import TwitchTokenRefresher

SRC = Path("src/twitch/token_refresher.py").read_text(encoding="utf-8")


@pytest.fixture
def refresher():
    r = TwitchTokenRefresher.__new__(TwitchTokenRefresher)
    r.accounts = {}
    return r


def _token_file(tmp_path, name="twitch_tokens_acct.txt", refresh=True):
    f = tmp_path / name
    body = "# header\nACCESS_TOKEN=aaa\n"
    if refresh:
        body += "REFRESH_TOKEN=rrr\n"
    f.write_text(body, encoding="utf-8")
    return str(f)


# ------------------------------------------------------------- the file

def test_the_file_is_used_when_present(refresher, tmp_path):
    assert refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=_token_file(tmp_path)) is True
    assert refresher.accounts["acct"]["refresh_token"] == "rrr"


# --------------------------------------------------------- the fallback

def test_a_missing_file_falls_back_to_env(refresher, tmp_path, monkeypatch):
    monkeypatch.setenv("MY_REFRESH", "from-env")
    assert refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=str(tmp_path / "gone.txt"),
        refresh_env_var="MY_REFRESH") is True
    assert refresher.accounts["acct"]["refresh_token"] == "from-env"


def test_a_file_without_a_refresh_token_falls_back_to_env(refresher, tmp_path, monkeypatch):
    monkeypatch.setenv("MY_REFRESH", "from-env")
    assert refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=_token_file(tmp_path, refresh=False),
        refresh_env_var="MY_REFRESH") is True
    assert refresher.accounts["acct"]["refresh_token"] == "from-env"


def test_the_file_wins_when_both_exist(refresher, tmp_path, monkeypatch):
    """The file is rewritten on every refresh, so it is the fresher source."""
    monkeypatch.setenv("MY_REFRESH", "from-env")
    refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=_token_file(tmp_path), refresh_env_var="MY_REFRESH")
    assert refresher.accounts["acct"]["refresh_token"] == "rrr"


def test_an_empty_env_var_is_not_a_token(refresher, tmp_path, monkeypatch):
    monkeypatch.setenv("MY_REFRESH", "   ")
    assert refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=str(tmp_path / "gone.txt"),
        refresh_env_var="MY_REFRESH") is False


def test_no_sources_at_all_fails_loudly(refresher, tmp_path, caplog):
    import logging
    with caplog.at_level(logging.ERROR, logger="twitch.token_refresher"):
        ok = refresher.register_account(
            account_name="acct", env_var_name="TOK",
            token_file_path=str(tmp_path / "gone.txt"))
    assert ok is False
    joined = " ".join(r.message for r in caplog.records)
    assert "will NOT auto-refresh" in joined, "silence here costs a stream"
    assert "expire mid-stream" in joined


def test_the_fallback_is_optional(refresher, tmp_path):
    """Callers that pass no env var keep the old file-only behaviour."""
    assert refresher.register_account(
        account_name="acct", env_var_name="TOK",
        token_file_path=_token_file(tmp_path)) is True


# ------------------------------------------------------------ bot wiring

def test_the_bot_passes_both_env_fallbacks():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "refresh_env_var='TWITCH_BROADCASTER_REFRESH_TOKEN'" in bot
    assert "refresh_env_var='TWITCH_REFRESH_TOKEN'" in bot


def test_the_broadcaster_fallback_matches_the_env_key_that_exists():
    """TWITCH_BROADCASTER_REFRESH_TOKEN sat in .env read by nothing; this is
    what finally consumes it. Checks the NAME only, never the value."""
    env = Path(".env")
    if not env.exists():
        pytest.skip("no .env in this checkout")
    names = {line.split("=", 1)[0].strip()
             for line in env.read_text(encoding="utf-8", errors="replace").splitlines()
             if "=" in line and not line.strip().startswith("#")}
    assert "TWITCH_BROADCASTER_REFRESH_TOKEN" in names


def test_no_token_value_is_ever_logged():
    """Every log line in the loader names a source or a variable, never a
    token."""
    block = SRC.split("def register_account")[1].split("\n    async def")[0]
    for bad in ("{refresh_token}", "{access_token}", "refresh_token!r", "access_token!r"):
        assert bad not in block, f"{bad} would put a credential in the log"

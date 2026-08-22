"""
Thin Twitch Helix helpers.

One place for the handful of REST calls the bot makes outside IRC/EventSub,
so callers don't each grow their own aiohttp boilerplate. Every function
returns a plain dict (or None on any failure) and logs the reason; nothing
here raises into the caller.

Tokens are passed in, not looked up, so these work with whichever token
the caller has (bot vs. broadcaster) and stay trivially testable by
monkeypatching `_request`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"


async def _request(
    method: str,
    path: str,
    *,
    client_id: str,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """Perform one Helix call. Returns the parsed JSON body or None."""
    if not client_id or not token:
        logger.warning("Helix %s %s skipped: missing client_id or token", method, path)
        return None
    headers = {
        "Client-Id": client_id,
        "Authorization": f"Bearer {token}",
    }
    url = f"{HELIX}/{path.lstrip('/')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (200, 202):
                    return await resp.json()
                body = await resp.text()
                if resp.status == 401:
                    logger.error("Helix %s %s: 401 — token rejected or missing scope: %s",
                                 method, path, body[:200])
                else:
                    logger.error("Helix %s %s: %s %s", method, path, resp.status, body[:200])
                return None
    except Exception as e:  # noqa: BLE001 — network helper, never raise upward
        logger.error("Helix %s %s failed: %s", method, path, e)
        return None


async def get_user_id(client_id: str, token: str, login: str) -> Optional[str]:
    """Resolve a login name to a user id."""
    login = (login or "").lower().lstrip("#")
    data = await _request("GET", "users", client_id=client_id, token=token,
                          params={"login": login})
    users = (data or {}).get("data") or []
    return users[0].get("id") if users else None


async def get_channel_info(client_id: str, token: str, broadcaster_id: str) -> Optional[Dict[str, Any]]:
    """Current category/title for a channel. Works with any valid token."""
    data = await _request("GET", "channels", client_id=client_id, token=token,
                          params={"broadcaster_id": broadcaster_id})
    rows = (data or {}).get("data") or []
    if not rows:
        return None
    row = rows[0]
    return {
        "game": row.get("game_name") or "",
        "game_id": row.get("game_id") or "",
        "title": row.get("title") or "",
        "login": row.get("broadcaster_login") or "",
    }


async def create_clip(client_id: str, token: str, broadcaster_id: str) -> Optional[Dict[str, Any]]:
    """Clip the last ~30s of the live stream.

    Needs a user token with the `clips:edit` scope; the channel must be live.
    Returns {'id', 'url', 'edit_url'} or None. The clip takes a few seconds
    to finish processing after this returns."""
    data = await _request("POST", "clips", client_id=client_id, token=token,
                          params={"broadcaster_id": broadcaster_id}, timeout=5.0)
    rows = (data or {}).get("data") or []
    if not rows:
        return None
    clip_id = rows[0].get("id")
    if not clip_id:
        return None
    return {
        "id": clip_id,
        "url": f"https://clips.twitch.tv/{clip_id}",
        "edit_url": rows[0].get("edit_url") or "",
    }

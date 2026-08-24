from unittest.mock import AsyncMock

import pytest

from features.ad_announcer import AdAnnouncer


@pytest.mark.asyncio
async def test_ad_end_announcement_can_be_disabled():
    announcer = AdAnnouncer(twitch_client=None)
    announcer.ad_active = True
    announcer.update_settings({"announce_at_end": False})
    announcer._generate_return_message = AsyncMock()
    announcer._announce_ad = AsyncMock()

    await announcer._handle_ad_end({})

    assert announcer.ad_active is False
    announcer._generate_return_message.assert_not_awaited()
    announcer._announce_ad.assert_not_awaited()


@pytest.mark.asyncio
async def test_ad_end_announcement_remains_available():
    announcer = AdAnnouncer(twitch_client=None)
    announcer.ad_active = True
    announcer.update_settings({"announce_at_end": True})
    announcer._generate_return_message = AsyncMock(return_value="Welcome back!")
    announcer._announce_ad = AsyncMock()

    await announcer._handle_ad_end({})

    announcer._announce_ad.assert_awaited_once_with("Welcome back!", is_start=False)

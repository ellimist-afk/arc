"""Gift-sub bombs announce once, credit the gifter, and know the count.

Twitch sends a gift of N subs as N x channel.subscribe(is_gift=true), one per
RECIPIENT, plus one channel.subscription.gift naming the gifter. Arc was only
subscribed to the first kind and routed each one to the gift handler, which
read user_name (the recipient) as the gifter and `total` (absent on that
event) as 1 -- so a 10-sub gift produced ten messages crediting ten random
recipients with one sub each.
"""
import pytest

from features.event_announcer import EventAnnouncer


class FakeEngine:
    def __init__(self):
        self.scenarios = []

    async def generate_response(self, message, context, user, is_mention=False):
        self.scenarios.append((message, user))
        return {'text': f"reaction to: {message[:40]}"}


class FakeBot:
    def __init__(self):
        self.personality_engine = FakeEngine()
        self.config = {'TWITCH_CHANNEL': 'cassova_'}
        self.chat_buffer = None
        self.stream_info = None
        self.stream_recap = None
        self.notes = []
        self.session_summarizer = type('S', (), {
            'get_summary': lambda s, ch: '',
            'note_event': lambda s, ch, n: self.notes.append(n)})()


@pytest.fixture
def rig():
    bot = FakeBot()
    ann = EventAnnouncer(bot)
    announced = []

    async def cap(msg, priority="normal"):
        announced.append((priority, msg))
    ann._announce = cap
    return ann, bot, announced


def gift_event(user='kaycee', total=10, tier='1000', **kw):
    ev = {'user_name': user, 'total': total, 'tier': tier, 'is_anonymous': False}
    ev.update(kw)
    return ev


def recipient_event(name):
    return {'user_name': name, 'is_gift': True, 'tier': '1000'}


# ------------------------------------------------------- the reported case

async def test_ten_gift_subs_announce_once_with_the_gifter_and_count(rig):
    ann, bot, announced = rig
    for i in range(10):                       # the per-recipient events
        ann.note_gift_recipient(recipient_event(f"viewer{i}"))
    assert announced == [], "recipient events must be silent"

    await ann.handle_gift_sub(gift_event(user='kaycee', total=10))

    assert len(announced) == 1, "one gift, one announcement"
    scenario, actor = bot.personality_engine.scenarios[-1]
    assert actor == 'kaycee', "the GIFTER is the actor, not a recipient"
    assert '10' in scenario and 'subscriptions' in scenario
    assert 'big drop' in scenario, "a 10-sub bomb should read as a big deal"
    assert bot.notes[-1] == 'kaycee gifted 10 sub(s)'


async def test_recipients_are_drained_between_bombs(rig):
    ann, _, _ = rig
    for i in range(10):
        ann.note_gift_recipient(recipient_event(f"a{i}"))
    await ann.handle_gift_sub(gift_event(total=10))
    assert ann.gift_recipients == []

    ann.note_gift_recipient(recipient_event('b0'))
    await ann.handle_gift_sub(gift_event(user='someone', total=1))
    assert ann.gift_recipients == []


# ------------------------------------------------------------ field truth

async def test_single_gift_names_the_recipient(rig):
    ann, bot, _ = rig
    ann.note_gift_recipient(recipient_event('tinkysenpai'))
    await ann.handle_gift_sub(gift_event(user='kaycee', total=1))
    scenario, _ = bot.personality_engine.scenarios[-1]
    assert 'tinkysenpai' in scenario
    assert 'subscription ' in scenario and 'subscriptions' not in scenario


async def test_large_gift_does_not_list_every_recipient(rig):
    ann, bot, _ = rig
    for i in range(10):
        ann.note_gift_recipient(recipient_event(f"viewer{i}"))
    await ann.handle_gift_sub(gift_event(total=10))
    scenario, _ = bot.personality_engine.scenarios[-1]
    assert 'viewer7' not in scenario, "ten names would bloat the prompt"


async def test_anonymous_gifter_is_labelled_and_not_credited(rig):
    ann, bot, _ = rig
    await ann.handle_gift_sub({'user_name': None, 'total': 5,
                               'is_anonymous': True, 'tier': '1000'})
    scenario, actor = bot.personality_engine.scenarios[-1]
    assert actor == 'Anonymous'
    assert 'anonymous' in scenario.lower()


async def test_tier_is_reported(rig):
    ann, bot, _ = rig
    await ann.handle_gift_sub(gift_event(total=3, tier='3000'))
    scenario, _ = bot.personality_engine.scenarios[-1]
    assert 'Tier 3' in scenario


async def test_lifetime_total_is_mentioned_when_larger(rig):
    ann, bot, _ = rig
    await ann.handle_gift_sub(gift_event(total=5, cumulative_total=120))
    scenario, _ = bot.personality_engine.scenarios[-1]
    assert '120' in scenario

    await ann.handle_gift_sub(gift_event(total=5, cumulative_total=5))
    scenario2, _ = bot.personality_engine.scenarios[-1]
    assert 'in total' not in scenario2, "no lifetime line when it equals this gift"


@pytest.mark.parametrize('total, expected', [
    (None, 1), ('7', 7), (0, 1), (-3, 1), ('nonsense', 1),
])
async def test_malformed_counts_degrade_to_sane_values(rig, total, expected):
    ann, bot, announced = rig
    await ann.handle_gift_sub(gift_event(total=total))
    assert len(announced) == 1
    assert bot.notes[-1] == f'kaycee gifted {expected} sub(s)'


# ------------------------------------------------------------- regression

async def test_recipient_events_never_reach_the_engine(rig):
    """The old routing sent these to handle_gift_sub, producing one wrong
    announcement (and one LLM call) per recipient."""
    ann, bot, announced = rig
    for i in range(10):
        ann.note_gift_recipient(recipient_event(f"viewer{i}"))
    assert bot.personality_engine.scenarios == []
    assert announced == []


async def test_disabled_announcer_still_ignores_gifts(rig):
    ann, _, announced = rig
    ann.enabled = False
    await ann.handle_gift_sub(gift_event())
    assert announced == []

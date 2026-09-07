"""The ledger that says whether anything the co-host said actually landed.

Live 2026-09-06: "it's extremely unfunny too". Every fix before this asked
the model to be funnier. Nothing measured whether a line worked, so nothing
could tell an improvement from a regression -- or tell the bot to stop.

Chat measures it for free. These tests pin the measurement: who counts as
laughing, which line a laugh belongs to, and when a run of unprompted lines
that got nothing buys the room its quiet back.

Pure and clock-free: the ledger never calls now(), callers pass `at`.
"""
from features.laugh_ledger import LaughLedger, is_laugh

T = 1000.0


def _led(**kw):
    return LaughLedger(**kw)


def _with_line(seq=1, text="six bullets and a custody hearing", at=T,
               unsolicited=True, **kw):
    led = _led(**kw)
    led.note_bot_line(seq, text, at, unsolicited=unsolicited)
    return led


# --------------------------------------------------------- what is a laugh

def test_the_common_laughs_count():
    for text in ("lmao", "LMFAOOO", "haha", "hahaha", "LOOOL", "lol",
                 "kekw", "OMEGALUL", "lul", "pepelaugh", "icant", "hehe",
                 "\U0001F602", "\U0001F923", "\U0001F480"):
        assert is_laugh(text), text


def test_a_laugh_inside_a_word_is_not_a_laugh():
    """Crediting "lollipop" would score lines nobody reacted to at all."""
    for text in ("lollipop", "deadline", "dolores", "hahn", "kekwave", "lulled"):
        assert not is_laugh(text), text


def test_digits_and_underscores_are_not_word_boundaries():
    """The lookarounds used to be [a-z]-only, so a laugh word touching a
    digit or underscore still matched -- these are usernames and chat noise
    wearing a laugh, not a laugh."""
    for text in ("lol1", "1lol", "lol_bot", "@lol_gaming", "dead2"):
        assert not is_laugh(text), text


def test_rofl_counts():
    assert is_laugh("rofl")
    assert is_laugh("ROFLMAO")


def test_roflcopter_is_not_a_laugh():
    assert not is_laugh("roflcopter")


def test_dead_counts_only_as_a_whole_word():
    assert is_laugh("i'm dead")
    assert not is_laugh("deadline is friday")


def test_a_w_alone_is_too_ambiguous_to_count():
    assert not is_laugh("W")
    assert not is_laugh("w stream")


# ------------------------------------------------------------- crediting

def test_a_laugh_in_the_window_is_credited():
    led = _with_line()
    assert led.note_viewer("Chatter", "lmao", T + 5) == 1
    assert led.score(1) == 1


def test_the_same_viewer_laughing_twice_is_still_one_laugh():
    led = _with_line()
    led.note_viewer("chatter", "lmao", T + 5)
    assert led.note_viewer("CHATTER", "lmao", T + 8) is None
    assert led.score(1) == 1


def test_a_second_viewer_is_a_second_laugh():
    led = _with_line()
    led.note_viewer("chatter", "lmao", T + 5)
    assert led.note_viewer("someone_else", "KEKW", T + 20) == 1
    assert led.score(1) == 2


def test_a_laugh_after_the_window_belongs_to_nothing():
    led = _with_line()
    assert led.note_viewer("chatter", "lol", T + 31) is None
    assert led.score(1) == 0


def test_the_bot_never_laughs_at_itself():
    led = _with_line()
    assert led.note_viewer("elimist_", "lol", T + 5,
                           exclude={"elimist_", "cassova_"}) is None
    assert led.score(1) == 0


def test_the_streamers_own_voice_is_not_a_laughing_room():
    """His transcript arriving as a message would otherwise score every
    line the co-host says while he is talking."""
    led = _with_line()
    assert led.note_viewer("cassova_", "haha", T + 5,
                           exclude={"elimist_", "cassova_", "stream_audio"}) is None
    assert led.note_viewer("stream_audio", "hahaha", T + 6,
                           exclude={"elimist_", "cassova_", "stream_audio"}) is None
    assert led.score(1) == 0


def test_a_laugh_with_a_question_still_counts():
    led = _with_line()
    assert led.note_viewer("chatter", "lmao wait what game is this", T + 5) == 1


def test_a_laugh_before_the_line_belongs_to_nothing():
    led = _with_line()
    assert led.note_viewer("chatter", "lmao", T - 5) is None


def test_the_newest_line_in_the_window_wins():
    led = _with_line(seq=1, text="first", at=T)
    led.note_bot_line(2, "second", T + 10, unsolicited=True)
    assert led.note_viewer("chatter", "lmao", T + 12) == 2
    assert led.score(1) == 0 and led.score(2) == 1


def test_a_message_that_is_not_a_laugh_credits_nothing():
    led = _with_line()
    assert led.note_viewer("chatter", "what game is this", T + 5) is None


def test_an_empty_ledger_credits_nothing():
    assert _led().note_viewer("chatter", "lmao", T) is None


# ------------------------------------------------------------- the streak

def _three_dry(led=None, start=T):
    led = led or _led()
    for i in range(3):
        led.note_bot_line(i + 1, f"line {i}", start + i * 60, unsolicited=True)
    return led


def test_three_unprompted_lines_that_got_nothing_buys_quiet():
    led = _three_dry()
    now = T + 200
    assert led.dry_streak(now) == 3
    assert led.should_stay_quiet(now) is True


def test_a_line_that_landed_stops_the_streak():
    led = _three_dry()
    led.note_viewer("chatter", "lmao", T + 125)      # the newest line
    now = T + 200
    assert led.dry_streak(now) == 0
    assert led.should_stay_quiet(now) is False


def test_being_addressed_clears_the_streak():
    """A person talking to the co-host is the attention the rule waits for."""
    led = _three_dry()
    led.mark_engaged(T + 130)
    now = T + 200
    assert led.dry_streak(now) == 0
    assert led.should_stay_quiet(now) is False


def test_an_older_engagement_does_not_clear_a_later_streak():
    led = _led()
    led.mark_engaged(T)
    _three_dry(led, start=T + 10)
    assert led.dry_streak(T + 300) == 3


def test_a_line_still_inside_its_window_counts_neither_way():
    led = _three_dry()
    led.note_bot_line(4, "just said this", T + 190, unsolicited=True)
    assert led.dry_streak(T + 200) == 3, "the newest line has not had its chance"


def test_replies_are_skipped_not_counted():
    """Answering somebody is not volunteering; the rule is about the lines
    nobody asked for."""
    led = _led()
    led.note_bot_line(1, "unprompted one", T, unsolicited=True)
    led.note_bot_line(2, "an answer", T + 30, unsolicited=False)
    led.note_bot_line(3, "unprompted two", T + 60, unsolicited=True)
    assert led.dry_streak(T + 200) == 2


def test_two_dry_lines_are_not_yet_quiet():
    led = _led()
    for i in range(2):
        led.note_bot_line(i + 1, f"line {i}", T + i * 60, unsolicited=True)
    assert led.should_stay_quiet(T + 200) is False


def test_a_zero_quiet_after_never_holds_anything():
    led = _three_dry(_led(quiet_after=0))
    assert led.should_stay_quiet(T + 200) is False


# --------------------------------------------------------- the release

def test_quiet_holds_right_up_to_the_release():
    led = _three_dry()                    # newest dry line at T + 120
    assert led.should_stay_quiet(T + 120 + 599) is True


def test_release_lets_one_line_through_then_holds_again():
    led = _three_dry()
    released_now = T + 120 + 600
    assert led.should_stay_quiet(released_now) is False
    assert led.quiet_released_at() == released_now
    # Immediately after, before any new line goes out, quiet resumes.
    assert led.should_stay_quiet(released_now) is True


def test_a_released_line_that_gets_a_laugh_clears_the_streak():
    led = _three_dry()
    released_now = T + 120 + 600
    assert led.should_stay_quiet(released_now) is False
    led.note_bot_line(4, "released line", released_now, unsolicited=True)
    led.note_viewer("chatter", "lmao", released_now + 1)
    assert led.dry_streak(released_now + 700) == 0


def test_release_seconds_zero_never_releases():
    led = _three_dry(_led(release_s=0))
    assert led.should_stay_quiet(T + 120 + 10_000) is True


def test_quiet_released_at_is_none_until_a_release_fires():
    led = _three_dry()
    assert led.quiet_released_at() is None
    led.should_stay_quiet(T + 120 + 599)   # still holding, no release yet
    assert led.quiet_released_at() is None


# ------------------------------------------------------------ what it says

def test_recent_returns_only_settled_lines_newest_first():
    led = _led()
    led.note_bot_line(1, "one", T, unsolicited=True)
    led.note_bot_line(2, "two", T + 60, unsolicited=True)
    led.note_bot_line(3, "too fresh", T + 190, unsolicited=True)
    got = led.recent(4, before=T + 200)
    assert [line.text for line in got] == ["two", "one"]


def test_recent_is_capped():
    led = _led()
    for i in range(6):
        led.note_bot_line(i + 1, f"line {i}", T + i * 60, unsolicited=True)
    assert len(led.recent(4, before=T + 1000)) == 4


def test_best_ranks_by_laughs_then_recency():
    led = _led()
    for i, text in enumerate(("quiet one", "landed once", "landed twice")):
        led.note_bot_line(i + 1, text, T + i * 60, unsolicited=True)
    led.note_viewer("a", "lmao", T + 61)
    led.note_viewer("a", "lmao", T + 121)
    led.note_viewer("b", "kekw", T + 122)
    assert [line.text for line in led.best(5)] == ["landed twice", "landed once"]


def test_the_history_is_a_ring():
    led = _led(history=3)
    for i in range(5):
        led.note_bot_line(i + 1, f"line {i}", T + i, unsolicited=True)
    assert led.stats()['lines'] == 3
    assert led.score(1) == 0, "an evicted line is forgotten, not resurrected"
    assert led.line(5) is not None


def test_stats_counts_lines_laughs_and_the_streak():
    led = _three_dry()
    led.note_viewer("a", "lmao", T + 5)
    led.note_viewer("b", "kekw", T + 6)
    stats = led.stats()
    assert stats['lines'] == 3
    assert stats['laughed_lines'] == 1
    assert stats['laughs'] == 2
    assert stats['dry_streak'] == 2


def test_an_empty_line_is_not_recorded():
    led = _led()
    led.note_bot_line(1, "", T, unsolicited=True)
    led.note_bot_line(0, "no seq", T, unsolicited=True)
    assert led.stats()['lines'] == 0

"""Topic wells picker: the pure rows of the failure matrix (design s8)."""

import random

from features.topic_wells import (DEFAULT_OFF_TOPIC, WELLS, TopicWells,
                                  directive, off_topic_list)


def _all():
    return {w: {'x': w} for w in WELLS}


def _wells(*history, share=0.34, window=6):
    tw = TopicWells(game_share_max=share, window=window)
    for w in history:
        tw.note(w)
    return tw


def test_last_well_game_next_is_not_game():
    tw = _wells('game')
    for seed in range(20):
        assert tw.pick(_all(), random.Random(seed)).well != 'game'


def test_game_then_off_topic_excludes_both():
    tw = _wells('game', 'off_topic')
    for seed in range(20):
        assert tw.pick(_all(), random.Random(seed)).well not in ('game', 'off_topic')


def test_share_boundary_one_game_in_six_allows_another():
    tw = _wells('game', 'ask_chat', 'off_topic', 'streamer_said', 'earlier_tonight',
                'viewer_callback')
    assert tw.game_cap() == 2
    assert tw.game_allowed()


def test_share_boundary_two_games_in_six_blocks_game():
    tw = _wells('game', 'ask_chat', 'game', 'streamer_said', 'earlier_tonight',
                'viewer_callback')
    assert not tw.game_allowed()
    # The share cap binds only when there is an alternative well (F1); with
    # nothing else available and no gap given, game still fires.
    assert tw.pick({'game': {}}, random.Random(0)).well == 'game'
    # With an alternative present, the cap still blocks game.
    assert tw.pick({'game': {}, 'ask_chat': {}}, random.Random(0)).well == 'ask_chat'


def test_share_boundary_is_the_same_for_one_third_and_034():
    assert TopicWells(1 / 3, 6).game_cap() == TopicWells(0.34, 6).game_cap() == 2
    assert TopicWells(0.5, 6).game_cap() == 3


def test_only_game_available_fires_even_though_it_was_last():
    # F1: with game the only well with material, the never-twice-in-a-row
    # rule does not apply -- otherwise the bot goes permanently silent once
    # game is the sole well and was the last one noted.
    choice = _wells('off_topic', 'game').pick({'game': {}}, random.Random(0))
    assert choice.well == 'game'
    assert choice.reason == 'only well with material'


def test_only_game_available_fires_regardless_of_recent_history():
    choice = _wells('game', 'off_topic').pick({'game': {}}, random.Random(0))
    assert choice.well == 'game'


def test_solo_well_respects_the_min_gap():
    tw = TopicWells()
    tw.note('game', at=1000.0)
    # Too soon: default gap is 600s.
    assert tw.pick({'game': {}}, random.Random(0), now=1000.0 + 599) is None
    # At/after the gap: fires again.
    choice = tw.pick({'game': {}}, random.Random(0), now=1000.0 + 600)
    assert choice.well == 'game'


def test_solo_well_gap_check_needs_now_to_block():
    # Without `now` the module has nothing to compare against, so it stays
    # clock-free rather than guessing -- it does not block.
    tw = TopicWells()
    tw.note('game', at=1000.0)
    assert tw.pick({'game': {}}, random.Random(0)).well == 'game'


def test_solo_min_gap_applies_to_a_non_game_well_too():
    tw = TopicWells(solo_min_gap_s=300.0)
    tw.note('ask_chat', at=0.0)
    assert tw.pick({'ask_chat': {}}, random.Random(0), now=100.0) is None
    choice = tw.pick({'ask_chat': {}}, random.Random(0), now=300.0)
    assert choice.well == 'ask_chat'


def test_two_wells_available_old_rules_unchanged():
    # With an alternative present, the sole-well carve-out does not apply:
    # never-twice-in-a-row still holds.
    tw = _wells('ask_chat')
    choice = tw.pick({'ask_chat': {}, 'off_topic': {}}, random.Random(0),
                     now=0.0)
    assert choice.well == 'off_topic'


def test_only_game_last_was_off_topic_two_lines_ago_picks_game():
    tw = _wells('off_topic', 'ask_chat')
    choice = tw.pick({'game': {'on_screen': 'x'}}, random.Random(0))
    assert choice.well == 'game'
    assert choice.material == {'on_screen': 'x'}
    # game is the only well with material here, so the solo carve-out (F1)
    # picks it, not the multi-well "eligible" path.
    assert choice.reason == 'only well with material'


def test_nothing_available_is_none():
    assert TopicWells().pick({}, random.Random(0)) is None


def test_prefers_least_recently_used():
    tw = _wells('ask_chat', 'off_topic', 'streamer_said')
    avail = {w: {} for w in ('ask_chat', 'off_topic', 'streamer_said')}
    assert tw.pick(avail, random.Random(0)).well == 'ask_chat'


def test_never_used_ties_go_to_rng():
    seen = {TopicWells().pick(_all(), random.Random(s)).well for s in range(40)}
    assert len(seen) > 2


def test_screen_reaction_noted_as_game_counts():
    tw = TopicWells()
    tw.note('game')
    assert tw.history() == ['game']
    assert tw.stats()['game'] == 1
    tw.note('not-a-well')
    assert tw.history() == ['game']


def test_history_is_bounded_by_window():
    tw = _wells(*(['ask_chat', 'off_topic'] * 5), window=6)
    assert len(tw.history()) == 6
    assert tw.stats()['ask_chat'] == 5


def test_off_topic_never_repeats_back_to_back():
    tw = TopicWells()
    rng = random.Random(3)
    picks = [tw.pick_off_topic(['a', 'b', 'c'], rng) for _ in range(30)]
    assert all(x != y for x, y in zip(picks, picks[1:]))
    assert len(set(picks)) > 1


def test_off_topic_single_entry_still_works():
    tw = TopicWells()
    assert tw.pick_off_topic(['only'], random.Random(0)) == 'only'
    assert tw.pick_off_topic(['only'], random.Random(0)) == 'only'
    assert tw.pick_off_topic([], random.Random(0)) is None


def test_off_topic_list_is_tolerant():
    assert off_topic_list(None) == DEFAULT_OFF_TOPIC
    assert off_topic_list("food") == ["food"]
    assert off_topic_list("  ") == []
    assert off_topic_list(["a", "", None, 3, {"x": 1}]) == ["a", "3"]
    assert off_topic_list(42) == DEFAULT_OFF_TOPIC
    assert off_topic_list([]) == []


def test_bad_numbers_do_not_raise():
    tw = TopicWells(game_share_max="lots", window="six", solo_min_gap_s="soon")
    assert tw.window == 6 and tw.game_cap() == 2 and tw.solo_min_gap_s == 600.0


def test_game_cap_floors_at_one_when_share_positive():
    # F3: window 1/2/3 with the shipped 0.34 rounds to 0, which disabled
    # game forever with no sign. A positive share must never floor to 0.
    for window in (1, 2, 3):
        assert TopicWells(0.34, window).game_cap() == 1
    # A share of exactly 0 still means "never game".
    assert TopicWells(0.0, 2).game_cap() == 0
    # Unchanged: window 6 share 0.34 still gives 2.
    assert TopicWells(0.34, 6).game_cap() == 2


def test_directives_carry_material_and_steer_off_the_game():
    assert '"i hate this map"' in directive('streamer_said', {'quotes': ['i hate this map']})
    assert 'Do not mention the game' in directive('earlier_tonight', {'summary': 'pizza'})
    assert 'nothing to do with the game' in directive('ask_chat', {'names': ['ann']})
    assert 'ann is around' in directive('viewer_callback', {'viewer': 'ann', 'fact': 'bakes'})
    assert 'Say one line about: food' in directive('off_topic', {'topic': 'food'})
    assert directive('game', {}) == ''

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
    assert tw.pick({'game': {}}, random.Random(0)) is None


def test_share_boundary_is_the_same_for_one_third_and_034():
    assert TopicWells(1 / 3, 6).game_cap() == TopicWells(0.34, 6).game_cap() == 2
    assert TopicWells(0.5, 6).game_cap() == 3


def test_only_game_and_last_was_game_stays_quiet():
    assert _wells('off_topic', 'game').pick({'game': {}}, random.Random(0)) is None


def test_only_game_and_game_used_two_lines_ago_stays_quiet():
    assert _wells('game', 'off_topic').pick({'game': {}}, random.Random(0)) is None


def test_only_game_last_was_off_topic_two_lines_ago_picks_game():
    tw = _wells('off_topic', 'ask_chat')
    choice = tw.pick({'game': {'on_screen': 'x'}}, random.Random(0))
    assert choice.well == 'game'
    assert choice.material == {'on_screen': 'x'}
    assert 'eligible' in choice.reason


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
    tw = TopicWells(game_share_max="lots", window="six")
    assert tw.window == 6 and tw.game_cap() == 2


def test_directives_carry_material_and_steer_off_the_game():
    assert '"i hate this map"' in directive('streamer_said', {'quotes': ['i hate this map']})
    assert 'Do not mention the game' in directive('earlier_tonight', {'summary': 'pizza'})
    assert 'nothing to do with the game' in directive('ask_chat', {'names': ['ann']})
    assert 'ann is around' in directive('viewer_callback', {'viewer': 'ann', 'fact': 'bakes'})
    assert 'Say one line about: food' in directive('off_topic', {'topic': 'food'})
    assert directive('game', {}) == ''

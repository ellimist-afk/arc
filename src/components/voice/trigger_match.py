"""Voice trigger normalization for recognizer misfires.

Google's recognizer frequently renders a leading "hey b..." as "play ..."
('hey bud' -> 'play not', 'hey bot' -> 'play not'), so the exact-substring
trigger check never fires. This layer normalizes the transcript at the
trigger check only -- no recognizer or model changes.

Matching runs in three passes, cheapest first:
  1. exact      -- substring check against HEY_TRIGGERS (original behavior)
  2. normalized -- same check after replacing a misheard leading word
                   ('play'/'hay'/'they'/'a'/'okay') with 'hey'
  3. fuzzy      -- gated on the first token normalizing to 'hey': accept a
                   second token within edit distance 1 of a trigger word
                   ('not' -> 'bot')
"""
from typing import Sequence, Tuple

# Trigger phrases for "the streamer is addressing the bot". Single source of
# truth -- bot.py imports this list.
HEY_TRIGGERS = [
    'hey bot', 'hey talkbot', 'hey bud', 'hey buddy',
    'hey boss', 'hey there', 'hey elimist', 'yo bot',
    'ok bot', 'alright bot', 'listen bot',
    # Common misrecognitions of "hey bot"
    'hey bought', 'hey but', 'hey thought', 'hey bart',
    'hey bott', 'hay bot', 'hey bod', 'hey pot',
]

# Leading words the recognizer produces in place of "hey"
_HEY_MISHEARD_FIRST = {'play', 'hay', 'they', 'a', 'okay'}

# Canonical second tokens of the "hey <x>" triggers, for the fuzzy pass
_TRIGGER_SECOND_TOKENS = ('bud', 'buddy', 'boss', 'bot')


def _within_edit_distance_1(a: str, b: str) -> bool:
    """True if Levenshtein distance between a and b is 0 or 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    edited = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if edited:
            return False
        edited = True
        if la == lb:
            i += 1  # substitution
        j += 1      # insertion into the longer string
    return True


def normalize_leading_hey(text_lower: str) -> str:
    """Return text with a misheard leading word replaced by 'hey', or ''
    if the first word is not a known mishearing."""
    parts = text_lower.split(maxsplit=1)
    if not parts or parts[0] not in _HEY_MISHEARD_FIRST:
        return ''
    rest = parts[1] if len(parts) > 1 else ''
    return f'hey {rest}'.rstrip()


def match_hey_trigger(
    text_lower: str,
    triggers: Sequence[str] = HEY_TRIGGERS,
) -> Tuple[bool, str]:
    """Check a lowercased transcript against the 'hey ...' trigger list.

    Returns (matched, how) with how in {'exact', 'normalized', 'fuzzy',
    'none'}. Callers should log non-exact matches at INFO so false positives
    are diagnosable from the stream log.
    """
    if any(t in text_lower for t in triggers):
        return True, 'exact'

    normalized = normalize_leading_hey(text_lower)
    if normalized and any(t in normalized for t in triggers):
        return True, 'normalized'

    # Fuzzy pass, gated on the first token being (or normalizing to) 'hey'
    tokens = text_lower.split()
    if len(tokens) >= 2 and (tokens[0] == 'hey' or tokens[0] in _HEY_MISHEARD_FIRST):
        if any(_within_edit_distance_1(tokens[1], t) for t in _TRIGGER_SECOND_TOKENS):
            return True, 'fuzzy'

    return False, 'none'

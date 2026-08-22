"""
Repetition guard for co-host output.

WHY THIS EXISTS:
An LLM co-host running for hours drifts into loops: the same opener on
every reply ("oh chat, ..."), a catchphrase it liked once and now can't
drop, or a near-verbatim restatement of what it said three turns ago.
The system prompt asks for variety; this enforces it.

Three checks, all against the bot's OWN recent outputs (never viewer text):

1. n-gram similarity  - Jaccard overlap of bigram+trigram sets against each
                        recent output; max over the window is the score.
2. opening cooldown   - the first two content tokens must not match the
                        opening of any of the last N outputs.
3. phrase cooldown    - a trigram that has shown up in >= 2 recent outputs
                        is a "catchphrase"; reusing it while it's hot fails.

Pure Python, no I/O, no clock: the caller records accepted outputs and
asks for a verdict on candidates. Deterministic, so it's unit-testable
without a model in the loop.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Set, Tuple

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Trigrams made only of these carry no content; they never count as
# catchphrases ("so that is", "and then the").
_STOPWORDS = frozenset(
    """
    a an the and or but if so of to in on at for with from by as is are was
    were be been being it its it's this that these those i you he she we they
    me him her us them my your his our their what which who whom how when
    where why not no yes do does did have has had can could will would should
    just like about into than then there here up down out over
    """.split()
)


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens; punctuation dropped, apostrophes kept."""
    return _TOKEN_RE.findall(text.lower())


def ngrams(tokens: List[str], n: int) -> List[Tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _shingles(tokens: List[str]) -> Set[Tuple[str, ...]]:
    """Bigrams + trigrams as one set, so short and long overlap both count."""
    return set(ngrams(tokens, 2)) | set(ngrams(tokens, 3))


def _jaccard(a: Set, b: Set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_content_trigram(tri: Tuple[str, ...]) -> bool:
    return any(tok not in _STOPWORDS for tok in tri)


@dataclass
class RepetitionVerdict:
    ok: bool
    score: float                      # max n-gram similarity to a recent output
    reused_opening: Optional[str]     # "oh chat" if the opener is on cooldown
    hot_phrases: List[str]            # catchphrases the candidate reused
    nearest: Optional[str] = None     # the recent output it most resembles

    @property
    def reason(self) -> str:
        parts = []
        if self.reused_opening:
            parts.append(f"opening '{self.reused_opening}' reused")
        if self.hot_phrases:
            parts.append("hot phrases: " + ", ".join(repr(p) for p in self.hot_phrases))
        if self.nearest is not None and self.score > 0:
            parts.append(f"similarity {self.score:.2f}")
        return "; ".join(parts) or "ok"


@dataclass
class RepetitionGuard:
    """Scores candidate outputs against the bot's own recent history.

    history_size:          how many accepted outputs to remember
    similarity_threshold:  Jaccard over bigram+trigram sets at/above which a
                           candidate is a near-duplicate
    opening_cooldown:      how many recent outputs an opener stays hot for
    phrase_cooldown:       how many recent outputs a catchphrase stays hot for
    catchphrase_min_uses:  times a trigram must appear in the window before
                           it counts as a catchphrase
    short_text_tokens:     candidates with fewer tokens than this (e.g. dead-air
                           "anyone there") are compared by exact match only,
                           since n-gram overlap is meaningless that short
    """

    history_size: int = 20
    similarity_threshold: float = 0.45
    opening_cooldown: int = 5
    phrase_cooldown: int = 8
    catchphrase_min_uses: int = 2
    short_text_tokens: int = 4

    _history: Deque[str] = field(default_factory=deque, init=False, repr=False)
    _tokens: Deque[List[str]] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._history = deque(maxlen=self.history_size)
        self._tokens = deque(maxlen=self.history_size)

    # -------------------------------------------------------------- recording

    def record(self, text: str) -> None:
        """Remember an output the bot actually delivered."""
        toks = tokenize(text or "")
        if not toks:
            return
        self._history.append(text)
        self._tokens.append(toks)

    def clear(self) -> None:
        self._history.clear()
        self._tokens.clear()

    @property
    def history(self) -> List[str]:
        return list(self._history)

    # -------------------------------------------------------------- analysis

    def _opening(self, toks: List[str]) -> Optional[str]:
        if len(toks) < 2:
            return None
        return " ".join(toks[:2])

    def hot_openings(self) -> Set[str]:
        recent = list(self._tokens)[-self.opening_cooldown:]
        return {op for op in (self._opening(t) for t in recent) if op}

    def hot_phrases(self) -> Set[Tuple[str, ...]]:
        """Trigrams that recur across recent outputs (counted once per output)."""
        recent = list(self._tokens)[-self.phrase_cooldown:]
        counts: Counter = Counter()
        for toks in recent:
            for tri in set(ngrams(toks, 3)):
                if _is_content_trigram(tri):
                    counts[tri] += 1
        return {tri for tri, n in counts.items() if n >= self.catchphrase_min_uses}

    def check(self, candidate: str) -> RepetitionVerdict:
        toks = tokenize(candidate or "")
        if not toks or not self._tokens:
            return RepetitionVerdict(ok=True, score=0.0, reused_opening=None, hot_phrases=[])

        # Short text: only an exact restatement counts.
        if len(toks) < self.short_text_tokens:
            for past, past_toks in zip(self._history, self._tokens):
                if past_toks == toks:
                    return RepetitionVerdict(
                        ok=False, score=1.0, reused_opening=None,
                        hot_phrases=[], nearest=past,
                    )
            return RepetitionVerdict(ok=True, score=0.0, reused_opening=None, hot_phrases=[])

        cand_shingles = _shingles(toks)
        best_score, nearest = 0.0, None
        for past, past_toks in zip(self._history, self._tokens):
            s = _jaccard(cand_shingles, _shingles(past_toks))
            if s > best_score:
                best_score, nearest = s, past

        opening = self._opening(toks)
        reused_opening = opening if opening in self.hot_openings() else None

        cand_tris = set(ngrams(toks, 3))
        hot = [" ".join(tri) for tri in sorted(cand_tris & self.hot_phrases())]

        ok = (
            best_score < self.similarity_threshold
            and reused_opening is None
            and not hot
        )
        return RepetitionVerdict(
            ok=ok, score=best_score, reused_opening=reused_opening,
            hot_phrases=hot, nearest=nearest,
        )

    def avoid_hint(self, verdict: RepetitionVerdict) -> str:
        """Instruction block for a regeneration attempt after a failed check."""
        lines = ["Your previous draft repeated yourself. Rewrite it from scratch:"]
        if verdict.nearest and verdict.score >= self.similarity_threshold:
            lines.append(f"- It was nearly identical to something you already said: \"{verdict.nearest}\"")
        if verdict.reused_opening:
            lines.append(f"- Do not open with \"{verdict.reused_opening}\" again.")
        if verdict.hot_phrases:
            phrases = ", ".join(f"\"{p}\"" for p in verdict.hot_phrases)
            lines.append(f"- Retire these phrases, you overuse them: {phrases}")
        recent_openers = sorted(self.hot_openings())
        if recent_openers:
            lines.append("- Openers already used recently: " + ", ".join(f"\"{o}\"" for o in recent_openers))
        lines.append("- Same substance is fine; different words, different structure, different opener.")
        return "\n".join(lines)

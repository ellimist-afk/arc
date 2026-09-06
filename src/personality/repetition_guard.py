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
                        Bigrams count too, at a higher bar (>= 3 uses):
                        a two-word tic like "exactly how" recurs with a
                        different third word every time, so trigrams never
                        see it, while any two-word sequence lands often
                        enough by chance that 2 uses would over-trigger.
4. topic retell       - OPT-IN via check(fresh_topic=True), for lines where
                        the bot picks its own subject (dead-air fillers and
                        unprompted interjections): sharing two or more
                        distinctive content words with a single recent
                        output means the same bit re-told in new words,
                        which n-gram overlap cannot see. Words from the
                        message being answered are exempt via topic_exempt,
                        so riffing on what chat is actually discussing never
                        counts as a rerun. Direct mentions skip the check:
                        an addressed reply is owed whatever its topic.
5. target cooldown    - OPT-IN via check(target=...), for the one name the
                        co-host reaches for by reflex: the streamer's. Live
                        2026-09-06 every punchline in twenty minutes landed
                        on him whatever the topic was, and checks 1-4 saw
                        nothing (one token, otherwise different lines). If
                        the target already appeared in target_max of the
                        last target_window outputs, a draft naming him again
                        fails. Lines that are ABOUT him -- he was named in
                        the message being answered, or the line reacts to
                        his screen -- pass target_exempt=True and skip it.

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


# Frequent words that pass the content filter but identify no topic. Two
# lines sharing only these are not the same bit -- live 2026-08-27 the guard
# rejected drafts for "re-told topic: 'because', 'character'" and
# "'chatting', 'somehow'", silencing the co-host six times over vocabulary.
_COMMON = frozenset(
    """
    also actually again always another anyone anything around because been
    before better bit both call called came chat chatting come coming could
    days does doing done down each even ever every everyone everything feel
    feels felt find first from full game gave getting give given goes going
    gone good got great half happen happened hard here hold home hour hours
    idea keep kept kind know known last late later least left less let life
    like little long look looking looks lot made make makes making many
    maybe mean means might mind minute minutes more most move much name need
    needs never next nice night nothing now number often once only open
    other over own part people perfect person place play played playing
    point pretty probably put question read real really reason right room
    said same saw say says second see seem seems seen sense set show side
    since some somehow someone something sometimes soon sort sound sounds
    start started still stop stream stuff sure take taken talk talking tell
    than thing things think thought three time times today together told
    took total true try trying turn turned two under until used using very
    wait want wanted watch watching way ways week well went were what when
    while whole will with without word words work working world would year
    years yet your character characters
    """.split()
)


def _distinctive(tokens: List[str]) -> Set[str]:
    """Words that carry a topic: content words of 4+ letters ("waifu",
    "sigaren", "datacenter"). Short/function words never identify a bit."""
    return {t for t in tokens if len(t) >= 4 and t not in _STOPWORDS}


def _contains_run(tokens: List[str], run: List[str]) -> bool:
    """Do `run`'s tokens appear as a contiguous run inside `tokens`?"""
    if not run or len(run) > len(tokens):
        return False
    first = run[0]
    for i, tok in enumerate(tokens):
        if tok == first and tokens[i:i + len(run)] == run:
            return True
    return False


def names_target(text: str, target: Optional[str]) -> bool:
    """Does `text` name `target`? Token-level, so punctuation and case in a
    Twitch handle stop mattering ("Cassova_" names "cassova_"). A longer
    handle that merely starts with the target's tokens ("cassova_fan") does
    match -- accepted deliberately: those are near-always the streamer being
    talked about, and the cost of a miss (another reflex roast) beats the
    cost of a false pass."""
    if not target or not text:
        return False
    run = tokenize(target)
    return bool(run) and _contains_run(tokenize(text), run)


def _identifying(words: Set[str]) -> Set[str]:
    """Of some shared words, the ones specific enough to name a bit."""
    return {w for w in words if w not in _COMMON}


@dataclass
class RepetitionVerdict:
    ok: bool
    score: float                      # max n-gram similarity to a recent output
    reused_opening: Optional[str]     # "oh chat" if the opener is on cooldown
    hot_phrases: List[str]            # catchphrases the candidate reused
    nearest: Optional[str] = None     # the recent output it most resembles
    retold_words: List[str] = field(default_factory=list)  # topic words shared with retold_from
    retold_from: Optional[str] = None # the recent output whose topic was re-told
    overused_target: Optional[str] = None  # the roast target, when it was named too often
    target_uses: int = 0              # recent outputs that named the target
    target_window_seen: int = 0       # how many recent outputs were examined

    @property
    def reason(self) -> str:
        parts = []
        if self.overused_target:
            parts.append(f"streamer punchline: {self.overused_target} in "
                         f"{self.target_uses} of last {self.target_window_seen}")
        if self.reused_opening:
            parts.append(f"opening '{self.reused_opening}' reused")
        if self.hot_phrases:
            parts.append("hot phrases: " + ", ".join(repr(p) for p in self.hot_phrases))
        if self.retold_from:
            parts.append("re-told topic: " + ", ".join(repr(w) for w in self.retold_words))
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
    target_window:         how many recent outputs are searched for the roast
                           target's name
    target_max:            how many of those may name the target before a new
                           draft naming him is rejected
    """

    history_size: int = 20
    similarity_threshold: float = 0.45
    opening_cooldown: int = 5
    phrase_cooldown: int = 8
    catchphrase_min_uses: int = 2
    bigram_catchphrase_min_uses: int = 3
    short_text_tokens: int = 4
    topic_window: int = 3            # self-initiated lines look back this far
    topic_min_shared: int = 2        # shared distinctive words that mean "same bit"
    target_window: int = 4           # look back this many accepted outputs for the target
    target_max: int = 1              # target may appear in at most this many of them

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
        """N-grams that recur across recent outputs (counted once per output).

        Trigrams go hot at catchphrase_min_uses. Bigrams go hot at the
        higher bigram_catchphrase_min_uses -- they exist to catch two-word
        tics ("exactly how <anything>") whose changing tail hides them from
        the trigram count, and the higher bar keeps ordinary two-word
        collisions from tripping it."""
        recent = list(self._tokens)[-self.phrase_cooldown:]
        tri_counts: Counter = Counter()
        bi_counts: Counter = Counter()
        for toks in recent:
            for tri in set(ngrams(toks, 3)):
                if _is_content_trigram(tri):
                    tri_counts[tri] += 1
            for bi in set(ngrams(toks, 2)):
                if _is_content_trigram(bi):
                    bi_counts[bi] += 1
        hot = {tri for tri, n in tri_counts.items() if n >= self.catchphrase_min_uses}
        hot |= {bi for bi, n in bi_counts.items() if n >= self.bigram_catchphrase_min_uses}
        return hot

    @staticmethod
    def topic_words(text: str) -> Set[str]:
        """The distinctive words of a message, for check(topic_exempt=...)."""
        return _distinctive(tokenize(text or ""))

    def _retold_topic(
        self, toks: List[str], exempt: Set[str]
    ) -> Tuple[List[str], Optional[str]]:
        """Does this line re-tell the topic of a recent output?

        Compared per-output, not against the pooled window: two shared words
        scattered across different past lines are conversation, two shared
        with the SAME line are that line's premise again."""
        cand = _distinctive(toks) - exempt
        if len(cand) < self.topic_min_shared:
            return [], None
        recent = list(zip(self._history, self._tokens))[-self.topic_window:]
        best_words: List[str] = []
        best_past: Optional[str] = None
        for past, past_toks in recent:
            shared = cand & _distinctive(past_toks)
            # At least one shared word must actually name the bit. Two lines
            # sharing only frequent vocabulary ("because", "character") are
            # different lines about different things.
            if (len(shared) >= self.topic_min_shared
                    and _identifying(shared)
                    and len(shared) > len(best_words)):
                best_words, best_past = sorted(shared), past
        return best_words, best_past

    def _target_overuse(
        self, toks: List[str], target: Optional[str], target_exempt: bool
    ) -> Tuple[Optional[str], int, int]:
        """Has this target closed too many of the recent lines already?

        Only asked of drafts that name him, and only when the line is not
        about him (target_exempt). Short lines count too: "cassova_ again"
        is the same reflex as a long one."""
        if not target or target_exempt:
            return None, 0, 0
        run = tokenize(target)
        if not run or not _contains_run(toks, run):
            return None, 0, 0
        recent = list(self._tokens)[-self.target_window:]
        uses = sum(1 for past_toks in recent if _contains_run(past_toks, run))
        if uses >= self.target_max:
            return target, uses, len(recent)
        return None, uses, len(recent)

    def check(
        self,
        candidate: str,
        fresh_topic: bool = False,
        topic_exempt: Optional[Set[str]] = None,
        target: Optional[str] = None,
        target_exempt: bool = False,
    ) -> RepetitionVerdict:
        toks = tokenize(candidate or "")
        if not toks or not self._tokens:
            return RepetitionVerdict(ok=True, score=0.0, reused_opening=None, hot_phrases=[])

        overused_target, target_uses, target_seen = self._target_overuse(
            toks, target, target_exempt)

        # Short text: only an exact restatement counts.
        if len(toks) < self.short_text_tokens:
            for past, past_toks in zip(self._history, self._tokens):
                if past_toks == toks:
                    return RepetitionVerdict(
                        ok=False, score=1.0, reused_opening=None,
                        hot_phrases=[], nearest=past,
                        overused_target=overused_target,
                        target_uses=target_uses,
                        target_window_seen=target_seen,
                    )
            return RepetitionVerdict(
                ok=overused_target is None, score=0.0, reused_opening=None,
                hot_phrases=[], overused_target=overused_target,
                target_uses=target_uses, target_window_seen=target_seen,
            )

        cand_shingles = _shingles(toks)
        best_score, nearest = 0.0, None
        for past, past_toks in zip(self._history, self._tokens):
            s = _jaccard(cand_shingles, _shingles(past_toks))
            if s > best_score:
                best_score, nearest = s, past

        opening = self._opening(toks)
        reused_opening = opening if opening in self.hot_openings() else None

        cand_grams = set(ngrams(toks, 3)) | set(ngrams(toks, 2))
        hot = [" ".join(g) for g in sorted(cand_grams & self.hot_phrases())]

        retold_words: List[str] = []
        retold_from: Optional[str] = None
        if fresh_topic:
            retold_words, retold_from = self._retold_topic(toks, topic_exempt or set())

        ok = (
            best_score < self.similarity_threshold
            and reused_opening is None
            and not hot
            and retold_from is None
            and overused_target is None
        )
        return RepetitionVerdict(
            ok=ok, score=best_score, reused_opening=reused_opening,
            hot_phrases=hot, nearest=nearest,
            retold_words=retold_words, retold_from=retold_from,
            overused_target=overused_target, target_uses=target_uses,
            target_window_seen=target_seen,
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
        if verdict.retold_from:
            words = ", ".join(f"\"{w}\"" for w in verdict.retold_words)
            lines.append(f"- You already did the {words} bit: \"{verdict.retold_from}\". "
                         f"Pick a COMPLETELY different subject.")
        if verdict.overused_target:
            name = verdict.overused_target
            lines.append(
                f"- Leave {name} out of this one entirely: {name} was the punchline "
                f"in {verdict.target_uses} of your last {verdict.target_window_seen} "
                f"lines. The joke is about the topic at hand.")
        recent_openers = sorted(self.hot_openings())
        if recent_openers:
            lines.append("- Openers already used recently: " + ", ".join(f"\"{o}\"" for o in recent_openers))
        lines.append("- Same substance is fine; different words, different structure, different opener.")
        return "\n".join(lines)

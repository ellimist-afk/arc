# Sentence-streamed TTS

Speak as the model writes. Instead of *full completion → one TTS call → play*,
the reply is cut into sentences as tokens arrive, each sentence gets its own
TTS call, and playback of sentence N overlaps TTS of sentence N+1. Time to
first audio drops from the full generation+TTS time (1.5–3 s) to roughly
time-to-first-sentence + one short TTS call (~0.7–1.1 s).

Off by default. Enable in `bot_settings.json`:

```json
"tts_streaming": { "enabled": true, "min_sentence_chars": 12, "prefetch_depth": 2 }
```

Hot-reloaded with the rest of `bot_settings.json`.

## Pieces

| Piece | File | Role |
|---|---|---|
| `SentenceSplitter` | `src/audio/sentence_splitter.py` | Incremental, abbreviation-aware sentence cutting; holds too-short sentences; forces a cut on run-ons. Pure. |
| `UtterancePlayer` | `src/audio/utterance_player.py` | Two-stage pipeline: TTS producer → bounded queue → playback consumer. Skip-aware, failure-isolating. Pure (tts/play injected). |
| `StreamedReply` | `src/personality/streamed_reply.py` | Handle: `sentences` iterator in, `text`/`speech_text` out when `done`. |
| `PersonalityEngine.generate_response_streamed` | `src/personality/personality_engine.py` | Streams deltas, feeds the splitter, gates each sentence through the repetition guard, finalizes the reply from what was actually spoken. Lazy: no model call until something pulls. |
| `OptimizedAudioQueue.queue_utterance` | `src/audio/optimized_queue.py` | One queue item per streamed reply. Runs the player with the queue's cache-aware TTS and its off-loop chunked playback. |
| `ResponseCoordinator.coordinate_streamed_response` | `src/bot/response_coordinator.py` | Queues the utterance, awaits finalization, posts chat. |
| `TalkBot._try_streamed_reply` | `src/bot/bot.py` | Flag gate + "would this be spoken?" check; used by the chat and voice handlers ahead of the blocking path. |

## Behavior notes

- **One item = one reply.** The utterance is atomic in the queue: never merged
  with other audio (`_should_merge` refuses), never interleaved by a
  higher-priority item once it has started. Same guarantees as today.
- **Repetition guard.** The first sentence gets the full check *before any
  audio exists*; if it fails, the stream is abandoned and the blocking path
  (with its hinted regeneration) produces the reply, which is then streamed
  from text. Later sentences that near-duplicate recent output are dropped,
  not regenerated. The guard records what was actually spoken.
- **Chat text = spoken text.** `text` is assembled from the sentences that
  were yielded, so chat never shows a sentence that was dropped or cut off.
- **Timing modes don't apply.** Chat is posted when generation finishes
  (roughly as the last sentence's TTS starts). `chat_first` has no meaning
  when the text doesn't exist yet.
- **Skip** (`audio_queue.skip()`, voice "skip") interrupts the current clip
  *and* cancels the rest of the utterance.
- **Laziness.** The model isn't called until the queue reaches the item, so a
  backed-up queue doesn't burn tokens on replies that will expire. Expired
  items close their generator so the coordinator's `wait()` is released.
- **Failure.** A TTS failure skips that sentence; a stream failure after the
  first token ends the reply with what was said (no retry → no double-speak);
  a stream failure before the first token retries once. If streaming produces
  nothing at all, the bot falls through to the blocking path.
- **Cache.** Sentences are cached individually, which raises the hit rate for
  short, recurring sentences.

## Not yet

- Byte-streaming the TTS response itself (`with_streaming_response`) to start
  playback before a sentence's audio is fully generated. Next step once this
  is proven on stream; it lives inside `_play_audio`.
- The Realtime voice backend (`VOICE_BACKEND=realtime`, Phase 2 branch) is
  speech-to-speech and bypasses this path entirely. This is for chat replies,
  dead air, events, and the legacy voice path.

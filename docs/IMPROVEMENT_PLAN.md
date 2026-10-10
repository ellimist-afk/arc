# Improvement plan

Work items from the October 2026 audit. The quick fixes are already on
`claude/lucid-galileo-vdzeyh` (exact TTS cache, dashboard lockdown, LLM
timeouts, .env.example, requirements).

## How to run this

Run in waves. Within a wave, items are independent: run each in its own git
worktree and branch (`improve/<item>`), all branched from
`claude/lucid-galileo-vdzeyh`. When a wave is done, merge its branches into
`claude/lucid-galileo-vdzeyh` (resolve conflicts, run the full suite), then
start the next wave from the merged result.

- **Wave 1 (parallel):** 1, 2, 4, 5
- **Wave 2:** 3 (rewrites bot.py; conflicts with everything, so it runs alone)
- **Wave 3 (parallel):** 6a, 6b, 6c

For every item: keep the test suite green, add a regression test per fix
(each test file opens with a docstring saying what broke and why), commit in
small steps. If pyaudio / speech_recognition aren't installed, put
throwaway stub modules on PYTHONPATH (don't commit them). Line numbers below
are approximate.

---

## 1. Reliability bugs

1. Twitch IRC client, `src/twitch/twitch_client.py`:
   - Gives up after 5 reconnect attempts (~422, ~62s).
   - Reconnect is recursive (connect → _handle_messages → _handle_reconnect → connect, ~151/~432).
   - `reader.readline()` has no timeout (~147).
   - `_handle_messages` / `_ping_task` are untracked raw create_task calls (~96/99); ping loops pile up.
   - NOTICE "Login authentication failed" and RECONNECT are not handled.
   - The send cooldown (~131-138) isn't concurrency-safe.
   Replace with one supervised loop: unlimited capped jittered backoff, read timeout tied to PING, tracked tasks cancelled on reconnect, token refresh (`src/twitch/token_refresher.py`) on an auth NOTICE, a lock around sends.
2. Tasks leak past shutdown:
   - sys.path edits (bot.py, optimized_queue.py, personality_engine.py, core/database/manager.py) load modules twice, giving three TaskRegistry instances: bot.py ~248, `get_global_registry()` (twitch_client, eventsub_websocket, resilient_memory_system, ad_announcer, optimized_queue), and `src/bot/__init__.py get_registry()`.
   - `_shutdown_impl` (~bot.py:3348) only stops the bot's own registry. Make shutdown cancel everything tracked.
   - Route the remaining raw create_task calls through the registry: bot.py ~755, voice_commands.py ~285, realtime/backend.py ~229, recognition.py ~392.
   - Extend `tests/test_task_registry_call_sites.py`. (The full import cleanup is item 3.)
3. Dashboard WebSocket, `/ws` in `src/api/app.py` (~1097):
   - It calls `manager.connect()` on `src/api/websocket_manager.py`, which has no connect/disconnect.
   - `handle_config_update` imports the nonexistent `src.core.dynamic_config`.
   - Write a FastAPI-native manager that checks Origin against `settings.CORS_ORIGINS` and sends the message types the dashboard JS listens for.
   - Delete websocket_manager.py / websocket_handler.py if nothing else imports them.
4. `src/bot/response_coordinator.py` ~204 ignores `gather(..., return_exceptions=True)` results. Log the failures.
5. TTS OpenAI clients (`optimized_queue.py:86`, `tts_service.py:35`) use SDK defaults (600s, 2 retries). Add a timeout and max_retries=0, as with `LLM_REQUEST_TIMEOUT` in personality_engine.py. Update `tests/test_llm_endpoint.py`, which asserts the constructor text.
6. `src/twitch/helix.py` (~51-61): one shared session, back off on 429 via Ratelimit-Reset, refresh the token and retry once on 401.

## 2. Security

Already done: loopback bind, own-origin CORS, a cross-origin write refusal in `src/api/middleware/auth.py`, and a stub admin user in `src/core/jwt_auth.py` (`tests/test_dashboard_lockdown.py`).

1. Real local auth:
   - Generate a random dashboard token on first run (store it in .env or a 0600 file).
   - Require it on every state-changing route and on /ws. A cookie set via a one-time URL printed at startup is a good UX.
   - Replace the stubs. Remove or implement the hard-coded `ENABLE_JWT_AUTH=False` and app.py's import of the nonexistent `get_jwt_authenticator` (~1065).
   - Keep the Origin check.
2. XSS:
   - `{{ streamer_id }}` sits inside JS template literals ~45 times (dashboard.html ~718, pages-v2/settings.html ~358, stats.html ~705). Use `{{ streamer_id|tojson }}` in a const.
   - pages-v2/live.html ~337-340 puts chat into innerHTML. Use textContent.
   - Sweep static/js for other innerHTML use.
3. CSP: the strict policy in `src/api/middleware/csp.py` blocks base-v2.html's CDN and inline scripts. Vendor pinned libraries into /static (or pin + SRI) and add nonces.
4. Tokens:
   - `token_refresher.py`: absolute paths and 0600 for written files (~250-279), fix the bare excepts (~244, ~291).
   - `expires_at` starts as None, so the first refresh is 3h late. Call /oauth2/validate at startup and hourly.
   - Stop retrying an invalid refresh token forever.
   - `get_twitch_tokens.py`: add an OAuth state parameter, don't print tokens, chmod 600 the token file.
5. v2 endpoints return `HTTPException(500, detail=str(e))`; log and return a generic message instead. Make `GET /api/v2/settings/reload` a POST.
6. Delete the unused `src/twitch/eventsub_webhook.py` (it binds 0.0.0.0).
7. Run bandit on src/ and fix anything real.

## 3. Code structure (wave 2, behaviour-preserving, green after every commit)

1. Delete dead code (verify zero importers first):
   - `bot/intelligence/context/optimized_context_builder.py`
   - `services/decision_engine.py`
   - `services/response_generator.py`
   - `bot/joke_system.py`
   - `twitch/eventsub_webhook.py`
   - `api/monitoring_endpoints.py`
   - `services/registry_migration.py`
   - the stale `main()` in bot.py
   - Decide on `audio/tts_service.py` and `attention/replay.py` (used only by tests).
   - ServiceRegistry: registered 24 times and never read back; app.py ~370-400 imports 10 nonexistent modules, with errors swallowed. Remove it or make it real.
2. One package root: remove the sys.path hacks (bot.py, optimized_queue.py, personality_engine.py, core/database/manager.py, tests/conftest.py), use one import style, and add a test that fails if any module loads under two names.
3. One typed settings object, loaded once, reloadable and injected.
   - bot_settings.json is opened by 9 modules (7 times in bot.py).
   - `_load_bot_settings` / `_reload_settings` are near-duplicates.
   - Feature flags load twice with different defaults (bot.py ~3438 vs config_unified.py ~75).
   - There are three env loaders.
   - all_personalities.json is parsed 3 times.
   - Bug: `PersonalityPreset.CHAOTIC` expects a 'chaotic' key, but the JSON now has 'chaotic_old'.
   - Lean on `test_no_dead_settings.py` and `test_flag_sources_agree.py`.
4. Split bot.py along these seams:
   - setup/__init__ wiring
   - settings
   - chat pipeline (~940-1246)
   - voice pipeline (~1426-1635, ~1746-1987)
   - realtime (~1636-1745)
   - stream awareness (~2139-2327)
   - fillers (screen reactions, dead air, topic wells: ~2364-2978)
   - clips, raids, admin commands, API server, shutdown
   Merge the duplicated chat/voice context building (~1071-1085 vs ~1489-1502) into one `build_context` that returns a copy. Pass narrow interfaces to features (13 `self.bot` uses in src/features/).
5. Split personality_engine.py into prompt building, LLM transport/streaming and presets.
6. Fix the 6 bare excepts (personality_engine ~84/~261, token_refresher ~244/~291, metrics_collector ~178, websocket_handler ~221). Make the 18 `except Exception: pass` blocks log at debug.

## 4. Dashboard cleanup

Verify with Playwright screenshots of every routed page, before and after.

1. Delete unreachable templates (confirm first):
   - pages-v2/: settings-original, settings-backup, monitoring, production, stats, visualizer, commands, dashboard-simple
   - top level: dashboard-clean, dashboard-hybrid, settings-clean, onboarding, onboarding_wizard, command_builder, production_dashboard, memory.html, monitoring.html
   - components/: stream-monitor, circuit-breaker-monitor, connection-status
   - `should_use_v2_ui` is always True (`src/api/feature_flags.py:9`): remove the flag and the v1 branches/templates.
   - The mockup routes (app.py ~1752-1774) and base-clean.html.
   - Orphaned static files (settings-v2.css, analytics-realtime.js, chart-components.js, audio-dashboard.html, personality-settings-ux-fixes.js, navigation-integration-guide.html, cache-manager.js, NAVIGATION_MIGRATION_GUIDE.md, navigation-effects.css.bak).
   - Fold the *-fix.css sheets into their base sheets.
2. Broken routes: `/ui/v2/test` renders a missing template; pages extend `layouts-v2/*`, which doesn't exist.
3. The frontend calls ~84 /api paths; ~37 exist.
   - Add a test that every fetch/WS URL in routed templates and JS matches a route.
   - Then implement or delete the calling UI. Priorities: status, mute, settings, personality switch, memory.
   - The mute buttons call `/api/actions/mute` and `/api/v2/bots/mute`; point them at `/api/mute` and `/api/unmute`, which work.
4. Fake data:
   - `/metrics` has hard-coded counters and is registered twice.
   - `/api/health` always says healthy.
   - `emergency-generate` is keyword matching.
   - insights.html / live.html use Math.random() stats.
   - The rate limiter is a stub.
   Wire these to real data, remove them, or label them as unavailable.
5. app.py's `/api/settings/{id}`, `/api/v2/settings/{id}` and `/api/v2/settings/realtime/{id}` read the nonexistent `src.personality.runtime`. Wire them or remove them.
6. The root and /settings redirects use a hard-coded 'confusedamish' (~1590, ~1616). Use TWITCH_CHANNEL.
7. 16 v2 endpoints do `from src.api.app import app`. Use `src/api/dependencies.require_bot`.

## 5. Tooling, CI, docs

1. `tests/conftest.py`: install stub pyaudio/speech_recognition modules only when the real ones are missing. Add hardware/network markers. Fix the never-runnable whisper WAV tests (*.wav is gitignored).
2. `.github/workflows/ci.yml`:
   - Every step is continue-on-error. Make pytest and ruff blocking.
   - It runs a nonexistent `bot.py --dry-run`.
   - The build job has no Dockerfile; tests/performance is missing.
   - migrate.py applies only migrations/schema.sql (`messages` vs the code's `chat_messages`; the 2026-07-04 migration is skipped).
   - release.yml still says "talkbot".
3. Dependencies:
   - The pins don't install on Python 3.13 (numpy, pydantic, asyncpg); README says 3.10+, run.bat uses 3.11. Pick supported versions.
   - Split into core / voice / dev.
   - Add a pyproject.toml with ruff/black config.
   - run.bat is machine-specific. Add a cross-platform entry point.
4. Database: setup_database.py runs a schema that starts with `DROP TABLE ... CASCADE`, and there are three competing schemas. Make one baseline plus an idempotent migration runner, and a safe README step.
5. Docs:
   - The README's "no stream data ever leaves your computer" is false (chat, TTS and screenshots go to OpenAI/OpenRouter; the legacy ASR uses Google). Reuse ARC_REALTIME_REDESIGN.md §19 C4.
   - The README mentions pgvector (unused) and omits Redis/SQLite.
   - PRD.md and log lines say "TalkBot" with stale facts. Remove the talkbot-old/.env fallbacks.
6. Hard-coded personal defaults ('confusedamish', 'elimist_', the 'elimist' wake word, streamer_name in bot_settings.json): make them configuration and fail fast on missing required vars.
7. Add a test that every env var main.py reads appears in .env.example.

## 6a. Local-only mode (wave 3)

- Add a local TTS backend (Piper or Kokoro) behind the OpenAI TTS interface in `src/audio/optimized_queue.py`. Keep sentence streaming and the 24kHz mono int16 contract.
- `LOCAL_ONLY=true` refuses every cloud path: OpenAI TTS, the screen watcher (unless a local vision model is set), the Google ASR fallback and the realtime backend. Fail loudly if chat points at a non-local URL.
- Log at startup, and show in the dashboard, which external services are in use.
- Document it; test that LOCAL_ONLY blocks each cloud client.

## 6b. Decision timeline + latency metrics (wave 3)

- Record a structured event for every utterance and every skipped opportunity: stimulus, the guards/pacing rules that fired, model, tokens, text and timestamps. Keep a ring buffer plus a JSONL file per stream (this generalises `tools/audit_stream_log.py`).
- Capture the latency fields from ARC_REALTIME_REDESIGN.md §15 (p50/p95), expose them through metrics_collector, and replace the fake prometheus counters.
- A dashboard page that streams the timeline live, filterable by stimulus, plus p50/p95 for the last N turns. Add tests.

## 6c. Finish half-built features (wave 3, one commit each)

1. A `!forgetme` viewer opt-out: delete the viewer's memory rows and stop storing new ones.
2. Show the post-stream recap and laugh ledger in the dashboard.
3. Check auto_clip.clip_notable_moments is safe, then document how to enable it.
4. Settings/personality profile export and import (zip) via the API and the dashboard.
5. Plain-language status banners ("Twitch login expired", "OpenAI unreachable") driven by real health state.

Stop and report if any of these needs a design decision.

# Arc

An open-source, self-hostable AI co-host for Twitch streamers.

Arc turns solo streams into duo streams — it greets new followers, celebrates subs and raids, keeps chat engaged during ad breaks, and remembers your regulars. Everything runs on your own hardware, so your chat data stays with you.

---

## Why Arc

- **Self-hosted** — your chat, your bot account, your machine
- **Private** — no stream data ever leaves your computer
- **Extensible** — add your own features, personalities, and integrations
- **Voice-ready** — TTS built in, real-time voice interaction on the roadmap

## Core Features

- **Raid welcomes** — generates a unique, context-aware shoutout for every incoming raid, pulling the raider's stream info in real time
- **Ad break announcements** — smart messages that keep viewers around through ads
- **Personality system** — four built-in presets (Friendly, Sassy, Educational, Chaotic) plus fully custom trait sliders
- **Viewer memory** — remembers your regulars across streams
- **Voice + chat sync** — TTS and chat messages stay aligned, no awkward overlap
- **Dashboard** — local web UI to tune settings, personalities, and feature flags

## Tech

- Python 3.10+ / FastAPI
- PostgreSQL + pgvector for viewer memory
- OpenAI API for LLM and TTS
- Twitch EventSub WebSocket for real-time events

## Quick Start

1. Clone the repo and install dependencies:
   ```
   git clone https://github.com/ellimist-afk/arc.git
   cd arc
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:
   - `OPENAI_API_KEY`
   - `TWITCH_ACCESS_TOKEN`
   - `TWITCH_CLIENT_ID`
   - `TWITCH_CHANNEL`
   - `DATABASE_URL`

3. Set up the database:
   ```
   python setup_database.py
   ```

4. Run it:
   ```
   python main.py
   ```

5. Open the dashboard at `http://localhost:8000`

## Project Structure

```
arc/
├── src/
│   ├── bot/              # Main bot loop
│   ├── features/         # Individual features (raider welcome, ad announcer, etc.)
│   ├── twitch/           # Twitch client and EventSub
│   ├── personality/      # Personality engine
│   ├── services/         # Shared services (response generation, memory, etc.)
│   ├── audio/            # TTS and audio queue
│   ├── core/             # Resilience, task management
│   └── api/              # Dashboard web UI
├── migrations/           # Database schema
├── main.py               # Entry point
└── requirements.txt
```

## Status

Arc is in active development. Core features (chat, voice, raid welcomes, personality) are working. The public feature roadmap includes shoutouts, celebrations, smart scheduled messages, and eventually real-time voice co-hosting.

## License

MIT.

"""Realtime voice backend (VOICE_BACKEND=realtime), redesign doc §3.

Three focused modules, no framework:
  audio_router  -- single owner of realtime PCM in/out (doc §13)
  session       -- OpenAI Realtime WebSocket client (doc §7)
  backend       -- glue: stimuli -> AttentionRouter -> executed actions (§8/§9)
"""
from realtime.audio_router import AudioRouter, AudioDeviceError
from realtime.session import RealtimeVoiceSession
from realtime.backend import RealtimeVoiceBackend

__all__ = ["AudioRouter", "AudioDeviceError", "RealtimeVoiceSession",
           "RealtimeVoiceBackend"]

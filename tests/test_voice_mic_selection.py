"""Voice recognition must prefer the mic-only bus over the stream mix."""

from components.voice.recognition import VoiceRecognition


def test_b3_mic_only_bus_wins_over_b1_stream_mix(monkeypatch):
    devices = [
        "Microsoft Sound Mapper - Input",
        "Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)",
        "Samson Q2U Microphone",
        "Voicemeeter Out B3 (VB-Audio Voicemeeter VAIO)",
    ]
    monkeypatch.setattr(
        "components.voice.recognition.sr.Microphone.list_microphone_names",
        lambda: devices,
    )

    recognition = VoiceRecognition(asr_engine="google")

    assert recognition._find_voicemeeter_device() == 3


def test_b1_remains_fallback_when_b3_is_unavailable(monkeypatch):
    devices = [
        "Microsoft Sound Mapper - Input",
        "Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)",
        "Samson Q2U Microphone",
    ]
    monkeypatch.setattr(
        "components.voice.recognition.sr.Microphone.list_microphone_names",
        lambda: devices,
    )

    recognition = VoiceRecognition(asr_engine="google")

    assert recognition._find_voicemeeter_device() == 1

"""VAD ducking must monitor a mic-ONLY bus, not the full stream mix.

The bug: _find_microphone grabbed the first input device containing
"voicemeeter", which on this machine landed on "Voicemeeter Out A2" -- a bus
carrying mic + game + browser + discord. Game audio then falsely triggered
VAD and ducked TTS. A dedicated mic-only tap now exists: "Voicemeeter Out B3".

_find_microphone is called directly against a fake PyAudio; no real audio
device is ever opened.
"""
from audio.vad_ducking import VADDucking


class FakePyAudio:
    """Stands in for pyaudio.PyAudio -- serves a scripted device list."""

    def __init__(self, devices):
        # devices: list of (name, maxInputChannels)
        self._devices = devices

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, i):
        name, channels = self._devices[i]
        return {'name': name, 'maxInputChannels': channels}


def _vad_with_devices(devices):
    vad = VADDucking(audio_queue=None)
    vad.pyaudio = FakePyAudio(devices)
    return vad


def test_b3_present_is_picked_over_generic_voicemeeter():
    # A2 (the stream-mix bus) appears BEFORE B3 -- generic first-match would
    # wrongly grab A2; the priority list must reach past it to B3.
    devices = [
        ("Voicemeeter Out A2", 2),   # index 0 -- full stream mix, must NOT win
        ("Some Other Input", 1),     # index 1
        ("Voicemeeter Out B3", 2),   # index 2 -- dedicated mic-only tap
    ]
    assert _vad_with_devices(devices)._find_microphone() == 2


def test_b3_absent_falls_back_to_generic_voicemeeter():
    devices = [
        ("Line In", 2),              # index 0
        ("Voicemeeter Out A2", 2),   # index 1 -- only voicemeeter present
    ]
    assert _vad_with_devices(devices)._find_microphone() == 1


def test_no_voicemeeter_uses_best_physical_mic():
    # Tier 3: existing scoring behavior -- Samson beats a generic input.
    devices = [
        ("Line In", 1),              # index 0
        ("Samson Q2U Microphone", 1),  # index 1 -- preferred + "microphone"
    ]
    assert _vad_with_devices(devices)._find_microphone() == 1


def test_output_only_devices_are_skipped():
    # A B3 with zero input channels is an output endpoint and must be ignored.
    devices = [
        ("Voicemeeter Out B3", 0),   # index 0 -- output-only, not a real tap
        ("Voicemeeter Out A2", 2),   # index 1 -- valid voicemeeter input
    ]
    assert _vad_with_devices(devices)._find_microphone() == 1


def test_no_input_devices_returns_none():
    devices = [
        ("Speakers", 0),
    ]
    assert _vad_with_devices(devices)._find_microphone() is None

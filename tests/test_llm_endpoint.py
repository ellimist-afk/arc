"""Chat can move to another OpenAI-compatible endpoint (OpenRouter, for Grok)
without dragging TTS along.

Before LLM_API_KEY existed, the only way to point chat elsewhere was
OPENAI_BASE_URL, and the OpenAI SDK reads that env var in EVERY client it
builds: TTS would have gone to OpenRouter too, with the OpenRouter key.
"""
import logging

from personality.personality_engine import PersonalityEngine


def test_llm_key_moves_chat_to_its_own_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    e = PersonalityEngine(memory_system=None, openai_api_key="sk-openai",
                          llm_api_key="sk-or-test",
                          llm_base_url="https://openrouter.ai/api/v1")
    assert e.openai_client.api_key == "sk-or-test"
    assert str(e.openai_client.base_url).startswith("https://openrouter.ai/api/v1")
    assert e.llm_endpoint_host() == "openrouter.ai"


def test_llm_key_ignores_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    e = PersonalityEngine(memory_system=None, openai_api_key="sk-openai",
                          openai_base_url="http://h:1/v1",
                          llm_api_key="sk-or-test",
                          llm_base_url="https://openrouter.ai/api/v1")
    assert e.openai_base_url == "https://openrouter.ai/api/v1"


def test_without_llm_key_behaviour_is_unchanged(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    e = PersonalityEngine(memory_system=None, openai_api_key="sk-openai",
                          llm_base_url="https://openrouter.ai/api/v1")
    assert e.openai_client.api_key == "sk-openai"
    assert e.openai_base_url is None
    assert e.llm_endpoint_host() == "api.openai.com"


def test_startup_line_names_host_and_model_never_the_key(monkeypatch, caplog):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with caplog.at_level(logging.INFO, logger="personality.personality_engine"):
        e = PersonalityEngine(memory_system=None, llm_api_key="sk-or-secret-123",
                              llm_base_url="https://openrouter.ai/api/v1")
    lines = [r.getMessage() for r in caplog.records if "LLM endpoint" in r.getMessage()]
    assert len(lines) == 1
    assert "openrouter.ai" in lines[0] and f"model={e.llm_model}" in lines[0]
    assert "sk-or-secret-123" not in caplog.text


def test_tts_clients_are_pinned_to_openai(monkeypatch):
    """An OPENAI_BASE_URL in the env must not redirect speech."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    from pathlib import Path
    from openai import AsyncOpenAI
    src = Path(__file__).resolve().parent.parent / "src" / "audio"
    tts = (src / "tts_service.py").read_text(encoding="utf-8")
    queue = (src / "optimized_queue.py").read_text(encoding="utf-8")
    assert 'AsyncOpenAI(base_url="https://api.openai.com/v1")' in tts
    assert 'AsyncOpenAI(api_key=openai_api_key, base_url="https://api.openai.com/v1")' in queue
    # and an explicit base_url really does beat the env var
    assert str(AsyncOpenAI(base_url="https://api.openai.com/v1").base_url).startswith(
        "https://api.openai.com/v1")
    assert "openrouter" in str(AsyncOpenAI().base_url), "env var is honoured when unpinned"

"""An alert that always fires is not monitoring.

Across 14 logged sessions the slow_response alert count equalled the reply
count exactly -- it fired on every single reply, because the threshold was
500ms while gpt-5.5 answers in 1.3-3.3s. That trains you to ignore the
warning and buries the real stalls among the noise.

Also pinned here: TaskRegistry.create_task takes no `timeout`. Passing one
raised TypeError inside the chat handler and killed that reply (seen live
2026-08-25 and 2026-08-26). CLAUDE.md documented the bad call.
"""
import inspect
from pathlib import Path

import pytest

from monitoring.metrics_collector import MetricsCollector
from utils.task_registry import TaskRegistry


def _thresholds():
    m = MetricsCollector.__new__(MetricsCollector)
    MetricsCollector.__init__(m)
    return m.alert_thresholds


# ------------------------------------------------- the threshold is usable

def test_normal_replies_do_not_alert():
    """Measured gpt-5.5 latency: ~1.9s median, 1.3-3.3s normal."""
    limit = _thresholds()['response_time_ms']
    for normal_ms in (1300, 1900, 2537, 2807, 3300):
        assert normal_ms < limit, f"{normal_ms}ms is ordinary, not an alert"


def test_a_guard_retry_does_not_alert():
    """A repetition-guard regeneration is two calls, ~4s. Expected, not an
    anomaly -- alerting on it would restore the noise."""
    assert 4200 < _thresholds()['response_time_ms']


def test_a_real_stall_still_alerts():
    limit = _thresholds()['response_time_ms']
    for stalled_ms in (8000, 15000, 40000):
        assert stalled_ms > limit, f"{stalled_ms}ms must still be reported"


def test_the_key_says_what_it_measures():
    """It is compared against every individual sample, so calling it a p95
    misdescribed it."""
    t = _thresholds()
    assert 'response_time_ms' in t
    assert 'response_time_p95' not in t


def test_the_comparison_uses_the_renamed_key():
    src = Path("src/monitoring/metrics_collector.py").read_text(encoding="utf-8")
    assert "self.alert_thresholds['response_time_ms']" in src


def test_the_computed_p95_metric_is_untouched():
    """A separate thing with a similar name: the actual p95 on the snapshot."""
    src = Path("src/monitoring/metrics_collector.py").read_text(encoding="utf-8")
    assert "response_time_p95: float" in src, "the metric field must survive"


def test_the_other_thresholds_are_unchanged():
    t = _thresholds()
    assert t['error_rate'] == 0.05
    assert t['memory_mb'] == 500
    assert t['cpu_percent'] == 80
    assert t['audio_queue_length'] == 10


# ------------------------------------------- create_task has no timeout

def test_create_task_takes_no_timeout():
    params = inspect.signature(TaskRegistry.create_task).parameters
    assert 'timeout' not in params, \
        "passing timeout= raised TypeError and killed the chat reply"
    assert set(params) >= {'coro', 'name', 'cleanup'}


def test_no_caller_passes_a_timeout():
    for path in list(Path("src").rglob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        for chunk in src.split("create_task(")[1:]:
            call = chunk.split(")")[0]
            assert "timeout=" not in call, f"{path} passes timeout= to create_task"


def test_the_docs_do_not_teach_the_bad_call():
    """CLAUDE.md's example is where the bad call came from; it is local-only
    and gitignored, so skip cleanly when it is absent."""
    doc = Path("CLAUDE.md")
    if not doc.exists():
        pytest.skip("CLAUDE.md is local-only")
    text = doc.read_text(encoding="utf-8")
    block = text.split("self.task_registry.create_task(")[1].split(")")[0]
    assert "timeout=" not in block, "the documented example must not raise"

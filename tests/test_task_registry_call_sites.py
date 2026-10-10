"""Every task_registry.create_task() call passes only arguments it accepts.

Live 2026-09-29 01:27: the chat-burst auto-clip passed timeout=15.0, the
call raised TypeError, and the handler for that chat message died. Nothing
caught it before a stream because the line only runs when chat spikes.
"""
import ast
import inspect
from pathlib import Path

from utils.task_registry import TaskRegistry

ACCEPTED = set(inspect.signature(TaskRegistry.create_task).parameters) - {"self"}


def _bad_calls():
    bad = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_task"
                    and isinstance(node.func.value, (ast.Attribute, ast.Name))):
                continue
            owner = node.func.value
            owner_name = owner.attr if isinstance(owner, ast.Attribute) else owner.id
            if "task_registry" not in owner_name:
                continue
            for kw in node.keywords:
                if kw.arg is not None and kw.arg not in ACCEPTED:
                    bad.append(f"{path}:{node.lineno} {kw.arg}=")
    return bad


def test_no_call_passes_an_argument_create_task_does_not_take():
    assert _bad_calls() == [], "use asyncio.wait_for() for a deadline"

"""Tests for session_search transform_tool_result hooks in agent-runtime paths.

Verifies that post_tool_call fires BEFORE transform_tool_result in the
concurrent (agent_runtime_helpers) path.
"""

import pytest

_HOOK_LOG = []


def _fake_invoke_hook(name, **kwargs):
    global _HOOK_LOG
    _HOOK_LOG.append(name)
    if name == "transform_tool_result":
        return ["TRANSFORMED"]
    return []


@pytest.fixture(autouse=True)
def reset_log():
    global _HOOK_LOG
    _HOOK_LOG = []


class FakeAgent:
    session_id = "s1"
    quiet_mode = True
    valid_tool_names = set()

    def _get_session_db_for_recall(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        return db


def test_concurrent_path_hook_ordering(monkeypatch):
    """post_tool_call fires BEFORE transform_tool_result in concurrent path."""
    from agent.agent_runtime_helpers import invoke_tool

    agent = FakeAgent()

    # Patch hooks
    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: True)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    monkeypatch.setattr("model_tools._emit_post_tool_call_hook",
                        lambda **kw: _HOOK_LOG.append("post_tool_call"))

    result = invoke_tool(agent, "session_search", {"query": "test", "limit": 1},
                         effective_task_id="t1", tool_call_id="tc1")

    assert "post_tool_call" in _HOOK_LOG
    assert "transform_tool_result" in _HOOK_LOG
    post_idx = _HOOK_LOG.index("post_tool_call")
    transform_idx = _HOOK_LOG.index("transform_tool_result")
    assert post_idx < transform_idx, (
        f"post_tool_call ({post_idx}) must fire BEFORE transform_tool_result ({transform_idx})"
    )
    assert result == "TRANSFORMED"


def test_transform_hook_no_op_when_no_plugin(monkeypatch):
    """Result unchanged when no transform hook is registered."""
    from agent.agent_runtime_helpers import _apply_transform_tool_result_hook

    class FA:
        session_id = "s1"

    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: False)
    result = _apply_transform_tool_result_hook("session_search", {}, "ORIGINAL", FA())
    assert result == "ORIGINAL"

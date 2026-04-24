import json
from datetime import UTC, datetime, timedelta

import pytest

from gateway.builtin_hooks import action_log


@pytest.mark.asyncio
async def test_handle_writes_structured_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    await action_log.handle(
        "agent:end",
        {
            "platform": "webhook",
            "user_id": "907",
            "session_id": "sess-123",
            "message": (
                "Type: incident\n"
                "Source: glutax-backend\n"
                "Severity: critical\n"
                "Title: OCR receipt 502\n"
                "Summary: analyze-receipt failed\n"
            ),
            "response": (
                "Cause racine\n"
                "response.choices[0] accessed before guard\n\n"
                "Actions\n"
                "- Push envoyee\n"
                "- Tache creee\n"
            ),
        },
    )

    lines = (tmp_path / "hermes-actions.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["event"] == "agent:end"
    assert entry["platform"] == "webhook"
    assert entry["user_id"] == "907"
    assert entry["session_id"] == "sess-123"
    assert entry["topic"] == "OCR receipt 502"
    assert entry["event_context"]["source"] == "glutax-backend"
    assert entry["event_context"]["severity"] == "critical"
    assert "response.choices[0]" in entry["root_cause"]
    assert entry["actions"] == ["Push envoyee", "Tache creee"]


def test_prune_old_entries_keeps_recent_lines(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    log_path = tmp_path / "hermes-actions.log"
    log_path.write_text(
        json.dumps({"ts": old_ts, "topic": "old"}) + "\n" +
        json.dumps({"ts": recent_ts, "topic": "recent"}) + "\n",
        encoding="utf-8",
    )

    action_log._prune_old_entries(now=datetime.now(UTC))

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["topic"] == "recent"


def test_builtin_hook_registration_includes_action_log():
    from gateway.hooks import HookRegistry

    registry = HookRegistry()
    registry._register_builtin_hooks()

    assert any(hook["name"] == "action-log" for hook in registry.loaded_hooks)
    assert "agent:end" in registry._handlers

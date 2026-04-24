"""Built-in action log hook for durable cross-session recall.

Writes one JSONL entry per ``agent:end`` event to ``HERMES_HOME/hermes-actions.log``.
Entries are compact but structured so later sessions can recover what Hermes
did without relying on ephemeral notifications.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


LOG_FILE_NAME = "hermes-actions.log"
RETENTION_DAYS = 14
MAX_PREVIEW_LEN = 500
_WRITE_LOCK = threading.Lock()


def _log_path() -> Path:
    return get_hermes_home() / LOG_FILE_NAME


def _cleanup_stamp_path() -> Path:
    return get_hermes_home() / ".hermes-actions-cleaned-at"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _compact_preview(value: Any, limit: int = MAX_PREVIEW_LEN) -> str:
    text = str(value or "")
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _extract_labeled_value(text: str, labels: list[str]) -> str | None:
    if not text:
        return None
    for label in labels:
        match = re.search(
            rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
            text,
        )
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _extract_section(text: str, headings: list[str]) -> str | None:
    if not text:
        return None
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    target_headings = {heading.strip().lower() for heading in headings}
    stop_headings = {
        "actions",
        "action items",
        "next steps",
        "impact",
        "investigation",
        "contexte",
        "context",
        "summary",
        "resume",
        "résumé",
        "cause racine",
        "root cause",
    }

    capture = False
    captured: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        normalized_line = line.lstrip("#").strip().rstrip(":").strip().lower()
        if normalized_line in target_headings:
            capture = True
            captured = []
            continue
        if capture and normalized_line in stop_headings:
            break
        if capture:
            captured.append(raw_line)

    body = re.sub(r"\s+", " ", "\n".join(captured)).strip(" -\n\t")
    return body or None


def _extract_actions(text: str) -> list[str]:
    section = _extract_section(text, ["Actions", "Action items", "Next steps"])
    if not section:
        return []
    parts = [part.strip(" -") for part in re.split(r"(?:\s*[•\-]\s+|\s+\d+\.\s+)", section) if part.strip()]
    return parts[:6]


def _extract_topic(context: dict[str, Any]) -> str | None:
    message = str(context.get("message") or "")
    title = _extract_labeled_value(message, ["Title", "Sujet"])
    if title:
        return title
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "")
    if not first_line:
        return None
    return _compact_preview(first_line, 120)


def _event_context(message: str) -> dict[str, str]:
    fields = {
        "type": _extract_labeled_value(message, ["Type"]),
        "source": _extract_labeled_value(message, ["Source"]),
        "environment": _extract_labeled_value(message, ["Environment"]),
        "severity": _extract_labeled_value(message, ["Severity"]),
        "title": _extract_labeled_value(message, ["Title", "Sujet"]),
        "summary": _extract_labeled_value(message, ["Summary", "Resume", "Résumé"]),
        "fingerprint": _extract_labeled_value(message, ["Fingerprint"]),
        "requested_action": _extract_labeled_value(
            message,
            ["Requested action", "Action demandee", "Action demandée"],
        ),
    }
    return {key: value for key, value in fields.items() if value}


def _build_entry(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    message = str(context.get("message") or "")
    response = str(context.get("response") or "")
    entry = {
        "ts": _utc_now().isoformat(),
        "event": event_type,
        "status": "completed",
        "platform": context.get("platform"),
        "user_id": context.get("user_id"),
        "session_id": context.get("session_id"),
        "message_preview": _compact_preview(message),
        "response_preview": _compact_preview(response),
        "topic": _extract_topic(context),
    }

    parsed_context = _event_context(message)
    if parsed_context:
        entry["event_context"] = parsed_context

    root_cause = _extract_section(response, ["Cause racine", "Root cause"])
    if root_cause:
        entry["root_cause"] = root_cause

    actions = _extract_actions(response)
    if actions:
        entry["actions"] = actions

    return {key: value for key, value in entry.items() if value not in (None, "", [], {})}


def _prune_old_entries(now: datetime | None = None) -> None:
    now = now or _utc_now()
    log_path = _log_path()
    stamp_path = _cleanup_stamp_path()

    last_cleaned_at: datetime | None = None
    if stamp_path.exists():
        try:
            last_cleaned_at = datetime.fromisoformat(stamp_path.read_text(encoding="utf-8").strip())
        except ValueError:
            last_cleaned_at = None

    if last_cleaned_at and now - last_cleaned_at < timedelta(hours=12):
        return

    cutoff = now - timedelta(days=RETENTION_DAYS)
    retained_lines: list[str] = []

    if log_path.exists():
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                ts = datetime.fromisoformat(str(payload.get("ts", "")))
            except (json.JSONDecodeError, ValueError, TypeError):
                retained_lines.append(raw_line)
                continue
            if ts >= cutoff:
                retained_lines.append(raw_line)

        log_path.write_text(
            ("\n".join(retained_lines) + ("\n" if retained_lines else "")),
            encoding="utf-8",
        )

    stamp_path.write_text(now.isoformat(), encoding="utf-8")


def _append_entry(entry: dict[str, Any]) -> None:
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


async def handle(event_type: str, context: dict[str, Any]) -> None:
    """Persist a compact JSONL summary for completed agent runs."""
    if event_type != "agent:end":
        return

    with _WRITE_LOCK:
        _prune_old_entries()
        _append_entry(_build_entry(event_type, context))

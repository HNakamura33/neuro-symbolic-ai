"""JSONL conversation history → turn-structured text for ingestion.

`nsai ingest --jsonl` feeds conversation logs into the extract-then-store
pipeline. Raw JSONL is a poor LLM input (tool noise, nested envelopes, no
turn boundaries), so this module normalizes it deterministically first:
each recognized message becomes a numbered ``[turn N — role]`` block, and
everything that is not a user/assistant text message (tool calls, tool
results, metadata lines) is dropped.

Recognized line shapes, per line:

- ``{"role": "user", "content": "..."}``                       (flat)
- ``{"type": "user", "message": {"role": ..., "content": ...}}`` (Claude Code
  transcript envelope; any dict with a "message" object works)
- ``content`` may be a string or a list of content blocks, in which case only
  ``{"type": "text", "text": ...}`` blocks are kept.

Unparseable lines and messages without extractable text are skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_ROLES = ("user", "assistant", "system")


@dataclass
class Turn:
    index: int  # 1-based position among recognized turns
    role: str
    text: str


def _text_of(content: Any) -> str:
    """Extract plain text from a message content field (string or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return "\n".join(p.strip() for p in parts if p.strip())
    return ""


def _message_of(record: Any) -> tuple[str, str] | None:
    """Pull (role, text) out of one JSONL record; None if it isn't a message."""
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("message"), dict):
        record = record["message"]
    role = record.get("role")
    if role not in _ROLES:
        return None
    text = _text_of(record.get("content"))
    if not text:
        return None
    return role, text


def parse_jsonl_history(raw: str) -> list[Turn]:
    """Parse JSONL conversation history into recognized turns, in order."""
    turns: list[Turn] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = _message_of(record)
        if msg:
            turns.append(Turn(index=len(turns) + 1, role=msg[0], text=msg[1]))
    return turns


def format_history(raw: str) -> str:
    """Render JSONL history as numbered turn blocks for the extraction prompt.

    Raises ValueError when no message turns are recognized, so the CLI can
    fail loudly instead of ingesting an empty transcript.
    """
    turns = parse_jsonl_history(raw)
    if not turns:
        raise ValueError(
            "no user/assistant messages recognized in the JSONL file "
            "(expected lines with role/content or a nested message object)"
        )
    return "\n\n".join(f"[turn {t.index} — {t.role}]\n{t.text}" for t in turns)

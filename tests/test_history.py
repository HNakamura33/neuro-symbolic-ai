import json

import pytest

from nsai.history import format_history, parse_jsonl_history


def _jsonl(*records) -> str:
    return "\n".join(json.dumps(r) for r in records)


def test_flat_role_content_lines():
    raw = _jsonl(
        {"role": "user", "content": "Alice was born in Tokyo."},
        {"role": "assistant", "content": "Noted."},
    )
    turns = parse_jsonl_history(raw)
    assert [(t.index, t.role, t.text) for t in turns] == [
        (1, "user", "Alice was born in Tokyo."),
        (2, "assistant", "Noted."),
    ]


def test_claude_code_envelope_with_block_content():
    raw = _jsonl(
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "tool_use", "id": "t1", "name": "kb_find", "input": {}},
                    {"type": "text", "text": "second"},
                ],
            },
        },
    )
    turns = parse_jsonl_history(raw)
    assert turns[0].text == "hello"
    # tool_use blocks are dropped; text blocks join with a newline.
    assert turns[1].text == "first\nsecond"


def test_non_message_and_malformed_lines_are_skipped():
    raw = "\n".join(
        [
            "not json at all {",
            json.dumps({"type": "summary", "summary": "compacted"}),
            json.dumps({"role": "user", "content": ""}),  # no extractable text
            json.dumps({"role": "user", "content": "kept"}),
            "",
        ]
    )
    turns = parse_jsonl_history(raw)
    assert len(turns) == 1
    assert turns[0].text == "kept"
    assert turns[0].index == 1


def test_format_history_numbers_turns():
    raw = _jsonl(
        {"role": "user", "content": "Bob works at Acme."},
        {"role": "assistant", "content": "Stored."},
    )
    out = format_history(raw)
    assert "[turn 1 — user]\nBob works at Acme." in out
    assert "[turn 2 — assistant]\nStored." in out


def test_format_history_rejects_empty_transcript():
    with pytest.raises(ValueError):
        format_history(json.dumps({"type": "summary"}))

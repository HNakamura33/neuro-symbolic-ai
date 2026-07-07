from pathlib import Path

import pytest

from nsai.agent import CODING_TOOLS, _confirm_tool, build_options
from nsai.kb import KnowledgeBase
from nsai.tools import ALLOWED_TOOL_NAMES


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(tmp_path / "kb.ttl")


def test_default_mode_symbolic_only(kb: KnowledgeBase):
    opts = build_options(kb)
    assert opts.allowed_tools == ALLOWED_TOOL_NAMES + ["Task"]
    assert opts.permission_mode == "dontAsk"
    assert opts.can_use_tool is None


def test_coding_mode_adds_file_tools_but_gates_bash(kb: KnowledgeBase):
    opts = build_options(kb, coding=True)
    for name in ALLOWED_TOOL_NAMES + CODING_TOOLS + ["Task"]:
        assert name in opts.allowed_tools
    # Bash must NOT be auto-allowed: it goes through the confirmation callback.
    assert "Bash" not in opts.allowed_tools
    assert opts.can_use_tool is _confirm_tool
    assert opts.permission_mode is None


def test_full_auto_allows_bash_without_confirmation(kb: KnowledgeBase):
    opts = build_options(kb, coding=True, full_auto=True)
    assert "Bash" in opts.allowed_tools
    for name in ALLOWED_TOOL_NAMES + CODING_TOOLS + ["Task"]:
        assert name in opts.allowed_tools
    # No confirmation callback; everything outside the allowlist is denied.
    assert opts.can_use_tool is None
    assert opts.permission_mode == "dontAsk"


def test_full_auto_requires_coding_mode(kb: KnowledgeBase):
    with pytest.raises(ValueError):
        build_options(kb, full_auto=True)


@pytest.mark.asyncio
async def test_confirm_tool_denies_non_bash():
    result = await _confirm_tool("WebFetch", {"url": "https://x"}, None)
    assert result.behavior == "deny"


@pytest.mark.asyncio
async def test_confirm_tool_respects_user_answer(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    allowed = await _confirm_tool("Bash", {"command": "ls"}, None)
    assert allowed.behavior == "allow"

    monkeypatch.setattr("builtins.input", lambda _: "")
    denied = await _confirm_tool("Bash", {"command": "rm -rf /"}, None)
    assert denied.behavior == "deny"

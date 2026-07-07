from pathlib import Path

import pytest

from nsai.agent import build_options
from nsai.kb import KnowledgeBase
from nsai.subagents import build_agents
from nsai.tools import ALLOWED_TOOL_NAMES


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(tmp_path / "kb.ttl")


def test_default_mode_agents():
    agents = build_agents()
    assert set(agents) == {"symbolic-explorer", "kb-auditor", "prover"}


def test_coding_mode_adds_test_generator_and_loop_judge():
    agents = build_agents(coding=True)
    assert set(agents) == {
        "symbolic-explorer",
        "kb-auditor",
        "prover",
        "test-generator",
        "loop-judge",
    }


def test_explorer_and_auditor_are_read_only_and_cheap():
    agents = build_agents(coding=True)
    for name in ("symbolic-explorer", "kb-auditor"):
        agent = agents[name]
        assert agent.model == "haiku"
        assert "mcp__symbolic__kb_add_triples" not in agent.tools
        # Query-only: no file tools, no bash.
        assert all(t.startswith("mcp__symbolic__") for t in agent.tools)


def test_subagent_tools_exist_on_the_server():
    known = set(ALLOWED_TOOL_NAMES) | {"Read", "Bash"}
    for agent in build_agents(coding=True).values():
        for tool in agent.tools:
            assert tool in known, f"unknown tool {tool}"


def test_every_agent_has_prompt_and_description():
    for name, agent in build_agents(coding=True).items():
        assert agent.description, name
        assert agent.prompt, name


def test_options_wire_agents_and_task_tool(kb: KnowledgeBase):
    default = build_options(kb)
    assert "Task" in default.allowed_tools
    assert set(default.agents) == set(build_agents())

    coding = build_options(kb, coding=True)
    assert "Task" in coding.allowed_tools
    assert set(coding.agents) == set(build_agents(coding=True))
    # Bash stays out of allowed_tools even though loop-judge may request it:
    # it must keep flowing through the per-command confirmation callback.
    assert "Bash" not in coding.allowed_tools

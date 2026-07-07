"""Agent loop — wires the Claude Agent SDK to the symbolic tool server."""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import CanUseToolShadowedWarning, ToolPermissionContext
from rich.console import Console

from .kb import KnowledgeBase
from .prompts import CODE_SYSTEM_PROMPT, SYSTEM_PROMPT
from .subagents import build_agents
from .tools import ALLOWED_TOOL_NAMES, build_server

console = Console()

# File tools auto-allowed in coding mode. Bash is deliberately absent: it falls
# through to _confirm_tool so every command is confirmed by the user.
CODING_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "TodoWrite"]

# allowed_tools intentionally shadows the callback for the whole-tool entries
# above; the callback only gates what falls through (Bash and everything else).
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


async def _confirm_tool(
    tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    if tool_name != "Bash":
        return PermissionResultDeny(
            message=f"{tool_name} is not available in this session."
        )
    command = tool_input.get("command", "")
    console.print(f"[yellow]bash?[/yellow] [bold]{command}[/bold]")
    answer = await asyncio.to_thread(input, "  run this command? [y/N] ")
    if answer.strip().lower() in ("y", "yes"):
        return PermissionResultAllow()
    return PermissionResultDeny(message="User declined to run this command.")


def build_options(
    kb: KnowledgeBase,
    model: str | None = None,
    coding: bool = False,
    full_auto: bool = False,
    bypass: bool = False,
    subagents: bool = True,
) -> ClaudeAgentOptions:
    server = build_server(kb)
    # subagents=False is the experiment ablation (condition C1 in
    # docs/experiment-plan.md): same symbolic tools, no Task delegation.
    agents = build_agents(coding=coding) if subagents else None
    task_tool = ["Task"] if subagents else []
    if (full_auto or bypass) and not coding:
        raise ValueError("full_auto/bypass are only meaningful in coding mode")
    if coding:
        if bypass:
            # Bypass mode: NO allowlist, no gates — every tool the runtime
            # offers (including network) runs unconfirmed. Only for
            # disposable sandboxes; supersedes full_auto when both are set.
            return ClaudeAgentOptions(
                system_prompt=CODE_SYSTEM_PROMPT,
                model=model,
                mcp_servers={"symbolic": server},
                agents=agents,
                permission_mode="bypassPermissions",
                setting_sources=[],
            )
        if full_auto:
            # Full-auto mode: Bash runs without per-command confirmation.
            # Still an explicit allowlist — anything outside file tools,
            # Bash, Task, and the symbolic server (network etc.) is denied.
            return ClaudeAgentOptions(
                system_prompt=CODE_SYSTEM_PROMPT,
                model=model,
                mcp_servers={"symbolic": server},
                # dontAsk auto-allows every tool the runtime offers, so the
                # built-in set itself must be limited via tools= —
                # allowed_tools alone does not remove network/web access.
                tools=CODING_TOOLS + task_tool + ["Bash"],
                allowed_tools=ALLOWED_TOOL_NAMES + CODING_TOOLS + task_tool + ["Bash"],
                agents=agents,
                permission_mode="dontAsk",
                setting_sources=[],
            )
        # Hybrid mode: file tools + symbolic solvers. Bash is gated behind a
        # per-command user confirmation (also when a subagent runs it);
        # anything else is denied.
        return ClaudeAgentOptions(
            system_prompt=CODE_SYSTEM_PROMPT,
            model=model,
            mcp_servers={"symbolic": server},
            allowed_tools=ALLOWED_TOOL_NAMES + CODING_TOOLS + task_tool,
            agents=agents,
            can_use_tool=_confirm_tool,
            setting_sources=[],
        )
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        mcp_servers={"symbolic": server},
        # Task enables delegation to the subagents below; each subagent is
        # itself restricted to read-only symbolic tools by its definition.
        # tools= empties the built-in set (dontAsk auto-allows everything it
        # offers, so allowed_tools alone would leave file/bash/network open);
        # the symbolic MCP tools come in via mcp_servers regardless.
        tools=task_tool,
        allowed_tools=ALLOWED_TOOL_NAMES + task_tool,
        agents=agents,
        permission_mode="dontAsk",
        setting_sources=[],
    )


async def _render_response(client: ClaudeSDKClient, show_cost: bool = False) -> None:
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    console.print(block.text, markup=False)
                elif isinstance(block, ToolUseBlock):
                    short = block.name.removeprefix("mcp__symbolic__")
                    console.print(f"  ⚙ {short} {block.input}", style="dim")
        elif isinstance(message, ResultMessage):
            if message.is_error:
                console.print(f"[red]agent error: {message.subtype}[/red]")
            elif show_cost and message.total_cost_usd is not None:
                console.print(f"[dim]cost: ${message.total_cost_usd:.4f}[/dim]")


async def run_once(
    kb_path: Path,
    prompt: str,
    model: str | None = None,
    coding: bool = False,
    full_auto: bool = False,
    bypass: bool = False,
) -> None:
    """One-shot: send a single prompt and print the response."""
    kb = KnowledgeBase(kb_path)
    options = build_options(kb, model, coding=coding, full_auto=full_auto, bypass=bypass)
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        await _render_response(client, show_cost=True)


async def run_chat(
    kb_path: Path,
    model: str | None = None,
    coding: bool = False,
    full_auto: bool = False,
    bypass: bool = False,
) -> None:
    """Interactive multi-turn REPL sharing one session."""
    kb = KnowledgeBase(kb_path)
    options = build_options(kb, model, coding=coding, full_auto=full_auto, bypass=bypass)

    stats = kb.stats()
    if coding:
        if bypass:
            mode = "coding (bypass-permissions)"
        elif full_auto:
            mode = "coding (full-auto)"
        else:
            mode = "coding (hybrid)"
    else:
        mode = "neuro-symbolic agent"
    console.print(
        f"[bold]nsai[/bold] — {mode}  "
        f"[dim](KB: {kb_path}, {stats['triples']} triples — /exit to quit)[/dim]"
    )

    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = console.input("[bold cyan]you>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            if user_input in ("/exit", "/quit", "/q"):
                break
            await client.query(user_input)
            await _render_response(client)
            console.print()

    console.print("[dim]bye — KB saved.[/dim]")


VERIFY_TEMPLATE = """\
Fact-check the following claim against the knowledge base.

1. Decompose it into atomic [subject, predicate, object] triples.
2. Run kb_verify on each triple.
3. Report a table: triple / verdict / detail, then an overall assessment.
Remember: "unknown" means the KB is silent — say so, don't guess.

Claim: {claim}
"""


async def run_verify(kb_path: Path, claim: str, model: str | None = None) -> None:
    await run_once(kb_path, VERIFY_TEMPLATE.format(claim=claim), model)

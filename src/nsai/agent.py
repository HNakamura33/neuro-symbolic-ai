"""Agent loop — wires the Claude Agent SDK to the symbolic tool server."""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from rich.console import Console

from .kb import KnowledgeBase
from .prompts import SYSTEM_PROMPT
from .tools import ALLOWED_TOOL_NAMES, build_server

console = Console()


def build_options(kb: KnowledgeBase, model: str | None = None) -> ClaudeAgentOptions:
    server = build_server(kb)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        mcp_servers={"symbolic": server},
        allowed_tools=ALLOWED_TOOL_NAMES,
        # Deny everything not in allowed_tools: the agent gets ONLY the
        # symbolic tools — no file system, no bash, no network.
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


async def run_once(kb_path: Path, prompt: str, model: str | None = None) -> None:
    """One-shot: send a single prompt and print the response."""
    kb = KnowledgeBase(kb_path)
    options = build_options(kb, model)
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        await _render_response(client, show_cost=True)


async def run_chat(kb_path: Path, model: str | None = None) -> None:
    """Interactive multi-turn REPL sharing one session."""
    kb = KnowledgeBase(kb_path)
    options = build_options(kb, model)

    stats = kb.stats()
    console.print(
        f"[bold]nsai[/bold] — neuro-symbolic agent  "
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

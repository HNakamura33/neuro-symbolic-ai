"""nsai command-line interface."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .kb import KnowledgeBase

app = typer.Typer(help="Neuro-symbolic AI agent CLI", no_args_is_help=True)
kb_app = typer.Typer(help="Direct knowledge-base operations (no LLM)", no_args_is_help=True)
app.add_typer(kb_app, name="kb")

console = Console()

KBPathOption = typer.Option(
    None, "--kb", help="Path to the Turtle KB file (default: $NSAI_KB or ./kb.ttl)"
)
ModelOption = typer.Option(None, "--model", "-m", help="Model alias or full model id")


def _kb_path(value: Optional[Path]) -> Path:
    return value or Path(os.environ.get("NSAI_KB", "kb.ttl"))


# -- agent commands (LLM) -----------------------------------------------------


@app.command()
def chat(kb: Optional[Path] = KBPathOption, model: Optional[str] = ModelOption):
    """Interactive neuro-symbolic chat session."""
    from .agent import run_chat

    asyncio.run(run_chat(_kb_path(kb), model))


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Question or instruction"),
    kb: Optional[Path] = KBPathOption,
    model: Optional[str] = ModelOption,
):
    """One-shot question answered with KB + symbolic reasoning."""
    from .agent import run_once

    asyncio.run(run_once(_kb_path(kb), prompt, model))


@app.command()
def verify(
    claim: str = typer.Argument(..., help="Claim to fact-check against the KB"),
    kb: Optional[Path] = KBPathOption,
    model: Optional[str] = ModelOption,
):
    """Decompose a claim into triples and verify each against the KB."""
    from .agent import run_verify

    asyncio.run(run_verify(_kb_path(kb), claim, model))


@app.command()
def code(
    prompt: Optional[str] = typer.Argument(
        None, help="One-shot coding task (omit for an interactive session)"
    ),
    full_auto: bool = typer.Option(
        False,
        "--full-auto",
        help="Allow the coding tool set including bash without per-command "
        "confirmation (network etc. stays denied). Intended for sandboxes "
        "and experiment harnesses.",
    ),
    bypass: bool = typer.Option(
        False,
        "--bypass-permissions",
        help="DANGEROUS: no allowlist at all — every tool the runtime offers "
        "(including network) runs unconfirmed. Only for disposable sandboxes. "
        "Supersedes --full-auto.",
    ),
    kb: Optional[Path] = KBPathOption,
    model: Optional[str] = ModelOption,
):
    """Hybrid coding mode: file tools + symbolic verification (SMT/CSP/KB).

    File reads and edits run automatically; every bash command asks for
    confirmation first (unless --full-auto / --bypass-permissions).
    """
    from .agent import run_chat, run_once

    if bypass:
        console.print(
            "[red]bypass-permissions: ALL tools (including network) run "
            "WITHOUT confirmation — use only in a disposable sandbox.[/red]"
        )
    elif full_auto:
        console.print(
            "[yellow]full-auto: bash runs WITHOUT confirmation — "
            "use only in a sandbox or trusted workspace.[/yellow]"
        )
    if prompt:
        asyncio.run(
            run_once(
                _kb_path(kb), prompt, model, coding=True, full_auto=full_auto, bypass=bypass
            )
        )
    else:
        asyncio.run(
            run_chat(_kb_path(kb), model, coding=True, full_auto=full_auto, bypass=bypass)
        )


@app.command()
def ingest(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Text file to ingest"),
    jsonl: bool = typer.Option(
        False,
        "--jsonl",
        help="Treat FILE as JSONL conversation history: turns are recognized "
        "and numbered before extraction, tool noise is dropped.",
    ),
    kb: Optional[Path] = KBPathOption,
    model: Optional[str] = ModelOption,
):
    """Extract facts from a text file (or JSONL conversation history) into the KB."""
    from .agent import run_once

    raw = file.read_text(encoding="utf-8")
    if jsonl:
        from .history import format_history

        try:
            text = format_history(raw)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)
        prompt = (
            "The following is a conversation history, one numbered block per turn. "
            "Extract all durable factual statements into the knowledge graph with "
            "kb_add_triples (include schema triples like rdfs:subClassOf and "
            "owl:FunctionalProperty where implied). Skip greetings, questions, and "
            "retracted statements; prefer the latest version when a fact is corrected "
            "in a later turn. Set source to "
            f"'{file.name}#turn-N' using each fact's turn number. "
            "Then report how many triples you added and list them.\n\n"
            f"--- CONVERSATION ({file.name}) ---\n{text}"
        )
    else:
        prompt = (
            "Extract all factual statements from the following document and store them "
            "in the knowledge graph with kb_add_triples (include schema triples like "
            "rdfs:subClassOf and owl:FunctionalProperty where implied). "
            "Then report how many triples you added and list them.\n\n"
            f"--- DOCUMENT ({file.name}) ---\n{raw}"
        )
    asyncio.run(run_once(_kb_path(kb), prompt, model))


# -- direct KB commands (no LLM) -----------------------------------------------


@kb_app.command("add")
def kb_add(
    subject: str,
    predicate: str,
    obj: str = typer.Argument(..., metavar="OBJECT"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Provenance source"),
    kb: Optional[Path] = KBPathOption,
):
    """Add one triple directly."""
    store = KnowledgeBase(_kb_path(kb))
    added = store.add_triples([(subject, predicate, obj)], source=source)
    console.print("added." if added else "already present.")


@kb_app.command("show")
def kb_show(
    limit: int = typer.Option(100, help="Max triples to display"),
    kb: Optional[Path] = KBPathOption,
):
    """List triples in the KB."""
    store = KnowledgeBase(_kb_path(kb))
    rows = store.find(limit=limit)
    table = Table("subject", "predicate", "object")
    for s, p, o in rows:
        table.add_row(s, p, o)
    console.print(table)
    console.print(f"[dim]{len(rows)} shown / {store.stats()['triples']} total[/dim]")


@kb_app.command("query")
def kb_query(sparql: str, kb: Optional[Path] = KBPathOption):
    """Run a SPARQL SELECT/ASK query."""
    store = KnowledgeBase(_kb_path(kb))
    result = store.sparql(sparql)
    if isinstance(result, bool):
        console.print(str(result))
    elif not result:
        console.print("[dim]no results[/dim]")
    else:
        table = Table(*result[0].keys())
        for row in result:
            table.add_row(*(row.get(k, "") for k in result[0].keys()))
        console.print(table)


@kb_app.command("check")
def kb_check(
    subject: str,
    predicate: str,
    obj: str = typer.Argument(..., metavar="OBJECT"),
    kb: Optional[Path] = KBPathOption,
):
    """Verify one triple against the KB with OWL-RL inference."""
    store = KnowledgeBase(_kb_path(kb))
    r = store.verify_triple(subject, predicate, obj)
    color = {"entailed": "green", "contradicted": "red", "unknown": "yellow"}[r.verdict]
    console.print(f"[{color}]{r.verdict}[/{color}] — {r.detail}")


@kb_app.command("infer")
def kb_infer_cmd(kb: Optional[Path] = KBPathOption):
    """Count RDFS/OWL-RL inferred triples (in-memory only; kb.ttl is not modified)."""
    store = KnowledgeBase(_kb_path(kb))
    added = store.infer()
    console.print(
        f"inferred {added} new triples ({store.stats()['triples']} total, in-memory; "
        "kb.ttl unchanged — use 'kb export' to serialize the closure)."
    )


@kb_app.command("export")
def kb_export(
    dest: Path = typer.Argument(..., help="Output file"),
    fmt: str = typer.Option("turtle", "--format", "-f", help="turtle | nt | xml | json-ld | n3"),
    kb: Optional[Path] = KBPathOption,
):
    """Export the KB to an RDF file."""
    store = KnowledgeBase(_kb_path(kb))
    store.export(dest, fmt)
    console.print(f"exported {store.stats()['triples']} triples → {dest}")


@kb_app.command("import")
def kb_import(
    src: Path = typer.Argument(..., exists=True, readable=True, help="RDF file to merge"),
    kb: Optional[Path] = KBPathOption,
):
    """Merge triples from an RDF file into the KB."""
    store = KnowledgeBase(_kb_path(kb))
    added = store.import_file(src)
    console.print(f"imported {added} new triples ({store.stats()['triples']} total).")


@kb_app.command("source")
def kb_source(
    subject: str,
    predicate: str,
    obj: str = typer.Argument(..., metavar="OBJECT"),
    kb: Optional[Path] = KBPathOption,
):
    """Show provenance records for one triple."""
    store = KnowledgeBase(_kb_path(kb))
    records = store.provenance(subject, predicate, obj)
    if not records:
        console.print("[dim]no provenance recorded for this triple[/dim]")
        return
    table = Table("source", "at")
    for r in records:
        table.add_row(r["source"], r["at"])
    console.print(table)


@kb_app.command("build-from-code")
def kb_build_from_code(
    path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source file or directory to analyze"
    ),
    cpp_backend: str = typer.Option(
        "treesitter",
        "--cpp-backend",
        help="C/C++ parser backend: 'treesitter' (default, no build needed) or "
        "'clang' (higher precision; requires the nsai[clang] extra).",
    ),
    kb: Optional[Path] = KBPathOption,
):
    """Extract structural facts from source code into the KB (no LLM).

    Supports Python, C/C++, TypeScript/JavaScript, and Rust. Records imports,
    definitions, statically visible calls, class hierarchy, and raised
    exceptions, with per-file provenance. Re-running after code changes only
    adds new triples; stale contracts then surface as contradictions through
    `kb check`.
    """
    from .code2kb import extract_from_path

    store = KnowledgeBase(_kb_path(kb))
    files = 0
    skipped = 0
    total = 0
    for file, triples in extract_from_path(path, cpp_backend=cpp_backend):
        files += 1
        if not triples:
            skipped += 1
            console.print(f"[yellow]skipped (parse error): {file}[/yellow]")
            continue
        total += store.add_triples(triples, source=f"{file} (static-analysis)")
    console.print(
        f"scanned {files} files ({skipped} skipped), "
        f"added {total} new triples ({store.stats()['triples']} total)."
    )


@kb_app.command("stats")
def kb_stats_cmd(kb: Optional[Path] = KBPathOption):
    """KB size summary."""
    store = KnowledgeBase(_kb_path(kb))
    for k, v in store.stats().items():
        console.print(f"{k}: {v}")


if __name__ == "__main__":
    app()

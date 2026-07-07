"""Thin wrappers around py-tree-sitter shared by the C/C++, TS, and Rust extractors.

Languages come from tree-sitter-language-pack (prebuilt wheels, no compiler
needed). Queries use the tags.scm-style capture convention where practical.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from tree_sitter import Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language


@lru_cache(maxsize=None)
def _language(name: str):
    return get_language(name)


@lru_cache(maxsize=None)
def _query(name: str, source: str) -> Query:
    return Query(_language(name), source)


def parse(name: str, source: str) -> Node:
    """Parse source with the named grammar; return the tree's root node.

    tree-sitter never raises on malformed input — errors become ERROR nodes,
    so extraction degrades instead of failing.
    """
    return Parser(_language(name)).parse(source.encode("utf-8")).root_node


def matches(name: str, query_source: str, node: Node) -> Iterator[dict[str, list[Node]]]:
    """Run a query; yield the {capture-name: [nodes]} dict of each match."""
    for _, capt in QueryCursor(_query(name, query_source)).matches(node):
        yield capt


def text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")

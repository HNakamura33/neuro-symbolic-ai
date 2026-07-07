"""Precise C/C++ extractor via libclang (optional: ``pip install nsai[clang]``).

Same vocabulary and entity naming as `cpp.py`, but resolution goes through
clang's AST: calls resolve to the callee's declaration (cross-overload),
inheritance to the actual base declaration. Works without a build or
compile_commands.json, degrading where headers are missing (calls into
unseen headers are dropped — unlike the tree-sitter backend, which records
them verbatim by name).

Selected with ``nsai kb build-from-code --cpp-backend clang``.
"""

from __future__ import annotations

from pathlib import Path

from .base import path_module_name

EXTENSIONS = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}

module_name = path_module_name


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    raise NotImplementedError("libclang backend not implemented yet")  # TODO(agent:clang)

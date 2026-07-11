"""Static analysis → knowledge base (code2kb).

Extracts *structural* facts from source code deterministically — no LLM
involved — so the KB gets a complete, exact "ground" layer that LLM-extracted
contracts and invariants can sit on top of:

    (ns:pkg.mod_a,        ns:imports,       ns:pkg.mod_b)
    (ns:pkg.mod_a.f,      ns:defined_in,    ns:pkg.mod_a)
    (ns:pkg.mod_a.f,      ns:calls,         ns:pkg.mod_a.g)
    (ns:pkg.mod_a.ClassA, rdfs:subClassOf,  ns:pkg.mod_a.ClassB)
    (ns:pkg.mod_a.f,      ns:raises,        ns:ValueError)

Entities also get an rdf:type (ns:Module / ns:Class / ns:Function) so the
graph is queryable by kind.

Languages: Python (stdlib ast), C/C++, TypeScript/JavaScript, and Rust
(tree-sitter; C/C++ optionally via libclang — see `clang.py`). Each language
module implements the interface documented in `base.py`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from . import cpp, python, rust, typescript
from .python import extract_module_triples, module_name_for  # noqa: F401 (public API)

# Vendored/generated trees that would drown the KB in third-party facts.
_EXCLUDED_DIRS = {"node_modules", "target", "build", "dist", "__pycache__", "venv"}

CPP_BACKENDS = ("treesitter", "clang")


def _registry(cpp_backend: str = "treesitter"):
    """Map file suffix → language module for one scan."""
    if cpp_backend == "clang":
        from . import clang as cpp_module
    elif cpp_backend == "treesitter":
        cpp_module = cpp
    else:
        raise ValueError(f"unknown C/C++ backend: {cpp_backend!r} (expected {CPP_BACKENDS})")
    return {ext: mod for mod in (python, cpp_module, typescript, rust) for ext in mod.EXTENSIONS}


def iter_source_files(path: Path, suffixes: set[str]) -> Iterator[Path]:
    """Yield files under `path` (or `path` itself) with the given suffixes,
    in a stable order, skipping hidden and vendored directories."""
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            if Path(name).suffix in suffixes:
                yield Path(dirpath) / name


def extract_from_path(
    path: Path, cpp_backend: str = "treesitter"
) -> Iterator[tuple[Path, list[tuple[str, str, str]]]]:
    """Yield (file, triples) per source file; files that fail to parse yield []."""
    registry = _registry(cpp_backend)
    root = path if path.is_dir() else path.parent
    for file in iter_source_files(path, set(registry)):
        mod = registry[file.suffix]
        try:
            source = file.read_text(encoding="utf-8")
            triples = mod.extract(source, mod.module_name(file, root), path=file)
        except (SyntaxError, UnicodeDecodeError):
            triples = []
        yield file, triples

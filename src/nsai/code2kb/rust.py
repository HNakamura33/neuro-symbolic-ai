"""Rust extractor (tree-sitter `rust` grammar).

Entity naming: module = root-relative path (base.path_module_name), so
``src/lib.rs`` → ``src.lib``. ``::`` paths become dots (``use crate::x::y``
→ imports ``crate.x.y``). ``impl Trait for Type`` maps to rdfs:subClassOf
(Type, rdfs:subClassOf, Trait); struct/enum/trait all get rdf:type Class.
Rust has no exceptions, so `raises` is not emitted.
"""

from __future__ import annotations

from pathlib import Path

from .base import path_module_name

EXTENSIONS = {".rs"}

module_name = path_module_name


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    raise NotImplementedError("Rust extractor not implemented yet")  # TODO(agent:rust)

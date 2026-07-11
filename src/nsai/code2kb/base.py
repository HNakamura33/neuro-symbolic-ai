"""Shared pieces for code2kb language extractors.

Every language module exposes the same duck-typed interface:

    EXTENSIONS: set[str]                  # file suffixes it claims, e.g. {".rs"}
    module_name(path: Path, root: Path) -> str
    extract(source: str, module: str, path: Path | None = None)
        -> list[tuple[str, str, str]]     # deduplicated, ordered triples

and emits the common vocabulary (all names must survive NAME_RE, i.e.
kb.parse_term's bare-word rule — anything else is silently dropped):

    (module,  rdf:type,        Module)
    (entity,  rdf:type,        Class | Function)
    (entity,  defined_in,      module)
    (module,  imports,         module-or-package)
    (func,    calls,           func)          # statically visible, best effort
    (class,   rdfs:subClassOf, base)          # incl. interface impl / trait impl
    (func,    raises,          exception)     # where the language has exceptions
"""

from __future__ import annotations

import re
from pathlib import Path

# Only names that survive kb.parse_term's bare-word rule are recorded.
NAME_RE = re.compile(r"^[\w.-]+$")


def emit(triples: list[tuple[str, str, str]], s: str, p: str, o: str) -> None:
    """Append (s, p, o) if both names are representable in the KB."""
    if NAME_RE.match(s) and NAME_RE.match(o):
        triples.append((s, p, o))


def path_module_name(path: Path, root: Path) -> str:
    """Root-relative path as a dotted module id: src/utils/foo.ts → src.utils.foo.

    Used by the non-Python languages, where files (not packages) are the unit
    of modularity. Path chars outside NAME_RE become underscores.
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)
    parts = [*rel.parts[:-1], rel.stem]
    return ".".join(re.sub(r"[^\w-]", "_", part) for part in parts)

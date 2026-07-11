"""Precise C/C++ extractor via libclang (optional: ``pip install nsai[clang]``).

Same vocabulary and entity naming as `cpp.py` (``<module>.<ns>.<Class>.<method>``,
module = root-relative path), but everything goes through clang's real AST
instead of syntax:

  - Calls resolve through ``cursor.referenced`` to the callee's declaration,
    across overloads. The callee is module-prefixed when its definition (or,
    failing that, its declaration) lives in the parsed file, otherwise the
    bare ``::``-qualified name with dots (e.g. ``std.vector``).
  - Out-of-line members are the precision win over the tree-sitter backend:
    ``void Dog::bark() {}`` is qualified via ``semantic_parent`` to
    ``<module>.<ns>.Dog.bark`` even though the class body is elsewhere, and
    calls inside it (``speak()``) resolve to the exact qualified method.
  - Inheritance goes through ``CXX_BASE_SPECIFIER.referenced``, i.e. the
    actual base declaration, not whatever token followed the colon.

Each file is parsed as a single translation unit — no build and no
compile_commands.json required. The trade-off: when headers are missing
(unresolved ``#include``), extraction degrades gracefully rather than failing;
entities whose references cannot be resolved are dropped (the tree-sitter
backend records them verbatim by name instead). In particular the bundled
libclang wheel does not always locate platform system headers, so calls into
unseen standard-library code may be absent from the output. ``raises`` facts
come from ``CXX_THROW_EXPR`` cursors; if a libclang build does not expose that
cursor kind, throw sites are skipped silently.

Anonymous namespaces (empty spelling) are skipped in qualified names, and
names the KB cannot represent (destructors ``~X``, ``operator...``) are
filtered out by `base.emit`.

Selected with ``nsai kb build-from-code --cpp-backend clang``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import emit, path_module_name

EXTENSIONS = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}

module_name = path_module_name

_UNSAVED_NAME = "nsai_unsaved.cpp"


def _cindex():
    """Import clang.cindex lazily so the extra stays optional."""
    try:
        import clang.cindex as cindex
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "libclang backend requires the clang extra: pip install nsai[clang]"
        ) from exc
    return cindex


def _clang_args(path: Path | None) -> list[str]:
    """Compiler args by suffix: C for `.c`, C++17 for everything else."""
    if path is not None and path.suffix == ".c":
        return ["-x", "c", "-std=c11"]
    return ["-x", "c++", "-std=c++17"]


def _qualified(cursor, cindex) -> str:
    """Dotted qualified name via the semantic_parent chain (``a::B::c`` → ``a.B.c``).

    Skips the translation unit and anonymous scopes (empty spelling, e.g.
    anonymous namespaces); returns "" for unnamed cursors.
    """
    parts: list[str] = []
    cur = cursor
    while cur is not None and cur.kind != cindex.CursorKind.TRANSLATION_UNIT:
        if cur.kind.is_invalid():
            break
        if cur.spelling:
            parts.append(cur.spelling)
        cur = cur.semantic_parent
    return ".".join(reversed(parts))


def _entity_name(cursor, module: str, fname: str, cindex) -> str:
    """Name a referenced entity: module-prefixed iff it lives in the parsed file.

    Prefers the definition's location (an out-of-line definition in this file
    makes the entity local even if its declaration sits in a header)."""
    target = cursor.get_definition() or cursor
    qual = _qualified(target, cindex)
    if not qual:
        return ""
    loc = target.location.file
    if loc is not None and loc.name == fname:
        return f"{module}.{qual}"
    return qual


def _include_module(name: str) -> str:
    """``"a/b.h"`` → ``a.b``; ``<vector>`` → ``vector`` (spelling has no brackets)."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        return ""
    parts[-1] = re.sub(r"\.[^.]*$", "", parts[-1]) or parts[-1]
    return ".".join(parts)


def _include_spelling(cursor) -> str:
    """The include target as written; guarded because ``get_included_file()``
    raises AssertionError for unresolved includes (e.g. missing system headers)."""
    name = cursor.spelling
    if not name:
        try:
            included = cursor.get_included_file()
            name = included.name if included is not None else ""
        except AssertionError:
            return ""
    return name


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    """Extract structural triples from one C/C++ file via libclang.

    Parses `source` as a single, self-contained translation unit (when `path`
    is given, quoted includes resolve relative to it). Never hard-fails on
    invalid code — clang recovers and we extract what parsed. Raises
    RuntimeError only when libclang itself is unavailable.
    """
    cindex = _cindex()
    fname = str(path) if path is not None else _UNSAVED_NAME
    index = cindex.Index.create()
    try:
        tu = index.parse(
            fname,
            args=_clang_args(path),
            unsaved_files=[(fname, source)],
            options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )
    except cindex.TranslationUnitLoadError:
        return [(module, "rdf:type", "Module")]

    kinds = cindex.CursorKind
    function_kinds = {
        kinds.FUNCTION_DECL,
        kinds.CXX_METHOD,
        kinds.CONSTRUCTOR,
        kinds.FUNCTION_TEMPLATE,
    }
    class_kinds = {kinds.CLASS_DECL, kinds.STRUCT_DECL, kinds.CLASS_TEMPLATE}
    throw_kind = getattr(kinds, "CXX_THROW_EXPR", None)  # unexposed in some builds

    triples: list[tuple[str, str, str]] = [(module, "rdf:type", "Module")]

    def walk(cursor, current_fn: str | None, current_class: str | None) -> None:
        for child in cursor.get_children():
            loc = child.location.file
            if loc is None or loc.name != fname:
                continue  # from an included header — not this module's facts
            kind = child.kind

            if kind == kinds.INCLUSION_DIRECTIVE:
                target = _include_module(_include_spelling(child))
                if target:
                    emit(triples, module, "imports", target)

            elif kind in function_kinds and child.is_definition() and child.spelling:
                qualname = f"{module}.{_qualified(child, cindex)}"
                emit(triples, qualname, "rdf:type", "Function")
                emit(triples, qualname, "defined_in", module)
                walk(child, qualname, current_class)
                continue

            elif kind in class_kinds and child.is_definition() and child.spelling:
                qualname = f"{module}.{_qualified(child, cindex)}"
                emit(triples, qualname, "rdf:type", "Class")
                emit(triples, qualname, "defined_in", module)
                walk(child, current_fn, qualname)
                continue

            elif kind == kinds.CXX_BASE_SPECIFIER and current_class:
                ref = child.referenced
                if ref is not None:
                    base = _entity_name(ref, module, fname, cindex)
                    if base:
                        emit(triples, current_class, "rdfs:subClassOf", base)

            elif kind == kinds.CALL_EXPR and current_fn:
                ref = child.referenced
                if ref is not None:
                    callee = _entity_name(ref, module, fname, cindex)
                    if callee:
                        emit(triples, current_fn, "calls", callee)

            elif throw_kind is not None and kind == throw_kind and current_fn:
                thrown = next(iter(child.get_children()), None)  # bare `throw;` has none
                if thrown is not None:
                    decl = thrown.type.get_declaration()
                    if decl is not None and decl.spelling:
                        exc = _entity_name(decl, module, fname, cindex)
                    else:  # builtin type, e.g. `throw 42`
                        exc = thrown.type.spelling.replace("::", ".")
                    if exc:
                        emit(triples, current_fn, "raises", exc)

            walk(child, current_fn, current_class)

    walk(tu.cursor, None, None)
    return list(dict.fromkeys(triples))

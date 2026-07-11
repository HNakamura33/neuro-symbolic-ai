"""C/C++ extractor (tree-sitter `cpp` grammar — parses C too).

Entity naming: module = root-relative path (base.path_module_name);
functions/classes are qualified as ``<module>.<ns>.<Class>.<method>`` where
namespaces/classes are syntactically visible. ``#include "a/b.h"`` → imports
``a.b``; ``#include <vector>`` → imports ``vector``.

Caveats (inherent to syntax-only analysis, recorded as-is):
  - Only syntactically visible scope counts: out-of-line members
    (``void Dog::bark() {...}``) are resolved against classes defined in the
    same file; otherwise the qualified name is spliced onto the enclosing
    namespace path verbatim.
  - ``ns:calls`` covers direct calls whose callee is an identifier, a
    ``x.f`` / ``x->f`` field expression, or an ``A::f`` qualified name.
    Virtual dispatch, calls through pointers, and overload selection are
    out of scope; unresolved names are recorded verbatim.
  - Names that cannot be represented in the KB (``operator<<``, ``~Dog``,
    template ids like ``vec<int>``) are silently dropped by `base.emit`.

For higher precision (USR-based resolution, overloads), see the optional
libclang backend in `clang.py`.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .base import emit, path_module_name
from .treesitter import parse, text

EXTENSIONS = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}

module_name = path_module_name

# Node types that terminate a declarator chain with a (possibly qualified) name.
_NAME_TYPES = {
    "identifier",
    "field_identifier",
    "type_identifier",
    "qualified_identifier",
    "destructor_name",
    "operator_name",
}

_CLASS_TYPES = {"class_specifier", "struct_specifier"}


def _function_declarator(node: Node | None) -> Node | None:
    """Unwrap pointer/reference/... declarators down to a function_declarator."""
    while node is not None and node.type != "function_declarator":
        node = node.child_by_field_name("declarator")
    return node


def _declared_name(node: Node) -> Node | None:
    """Name node of a function-declaring definition/declaration, else None."""
    func = _function_declarator(node.child_by_field_name("declarator"))
    if func is None:
        return None
    name = func.child_by_field_name("declarator")
    while name is not None and name.type not in _NAME_TYPES:
        name = name.child_by_field_name("declarator")
    return name


def _parts(name: Node) -> list[str]:
    """``a::b::c`` (qualified or plain) as its non-empty dot-path segments."""
    return [p for p in text(name).replace("::", ".").split(".") if p]


class _Extractor:
    def __init__(self, module: str):
        self.module = module
        self.triples: list[tuple[str, str, str]] = []
        self.classes: dict[str, str] = {}  # simple class name -> qualified name
        self.methods: dict[str, set[str]] = {}  # class qualname -> member fn names
        self.functions: dict[str, str] = {}  # free fn name -> qualified name

    def _emit(self, s: str, p: str, o: str) -> None:
        emit(self.triples, s, p, o)

    def _resolve(self, parts: list[str]) -> str:
        """Qualified name, resolved against same-file classes when possible."""
        if parts and parts[0] in self.classes:
            return ".".join([self.classes[parts[0]], *parts[1:]])
        return ".".join(parts)

    # -- pass 1: symbol tables (classes, members, free functions) ---------------

    def collect(self, node: Node, prefix: str, class_qual: str | None) -> None:
        t = node.type
        if t == "namespace_definition":
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name is not None:
                prefix = ".".join([prefix, *_parts(name)])
            if body is None:
                return
            node = body
        elif t in _CLASS_TYPES:
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if body is None:
                return
            if name is not None and name.type == "type_identifier":
                qual = f"{prefix}.{text(name)}"
                self.classes.setdefault(text(name), qual)
                self.methods.setdefault(qual, set())
                prefix = class_qual = qual
            node = body
        elif t in ("function_definition", "field_declaration"):
            name = _declared_name(node)
            if name is not None:
                parts = _parts(name)
                if len(parts) == 1 and class_qual is not None:
                    self.methods[class_qual].add(parts[0])
                elif len(parts) == 1 and t == "function_definition":
                    self.functions.setdefault(parts[0], f"{prefix}.{parts[0]}")
                elif len(parts) == 2 and parts[0] in self.classes:
                    # out-of-line member: void Dog::bark() { ... }
                    self.methods.setdefault(self.classes[parts[0]], set()).add(parts[1])
        for child in node.named_children:
            self.collect(child, prefix, class_qual)

    # -- pass 2: triples ---------------------------------------------------------

    def walk(self, node: Node, prefix: str, class_qual: str | None, func: str | None) -> None:
        t = node.type
        if t == "preproc_include":
            self._include(node)
            return
        if t == "namespace_definition":
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name is not None:
                prefix = ".".join([prefix, *_parts(name)])
            if body is not None:
                self._walk_children(body, prefix, class_qual, func)
            return
        if t in _CLASS_TYPES:
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if body is None:
                return
            if name is not None and name.type == "type_identifier":
                qual = f"{prefix}.{text(name)}"
                self._emit(qual, "rdf:type", "Class")
                self._emit(qual, "defined_in", self.module)
                for clause in node.named_children:
                    if clause.type == "base_class_clause":
                        for base in clause.named_children:
                            self._base(qual, base)
                prefix = class_qual = qual
            self._walk_children(body, prefix, class_qual, func)
            return
        if t == "function_definition":
            qualname, body_class = self._def_qualname(node, prefix, class_qual)
            if qualname is not None:
                self._emit(qualname, "rdf:type", "Function")
                self._emit(qualname, "defined_in", self.module)
            body = node.child_by_field_name("body")
            if body is not None:
                self._walk_children(body, prefix, body_class, qualname or func)
            return
        if t == "call_expression" and func is not None:
            target = self._call_target(node.child_by_field_name("function"), class_qual)
            if target:
                self._emit(func, "calls", target)
        elif t == "throw_statement" and func is not None:
            expr = node.named_children[0] if node.named_children else None
            if expr is not None:
                if expr.type == "call_expression":
                    expr = expr.child_by_field_name("function")
                target = self._call_target(expr, class_qual)
                if target:
                    self._emit(func, "raises", target)
        self._walk_children(node, prefix, class_qual, func)

    def _walk_children(self, node: Node, prefix: str, class_qual: str | None, func: str | None) -> None:
        for child in node.named_children:
            self.walk(child, prefix, class_qual, func)

    # -- pieces ------------------------------------------------------------------

    def _include(self, node: Node) -> None:
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return
        raw = text(path_node).strip()
        raw = raw.strip('"') if path_node.type == "string_literal" else raw.strip("<>")
        if not raw:
            return
        p = PurePosixPath(raw)
        self._emit(self.module, "imports", ".".join([*p.parts[:-1], p.stem]))

    def _base(self, class_qual: str, base: Node) -> None:
        if base.type == "template_type":
            base = base.child_by_field_name("name") or base
        if base.type == "type_identifier":
            name = text(base)
            self._emit(class_qual, "rdfs:subClassOf", self.classes.get(name, name))
        elif base.type == "qualified_identifier":
            self._emit(class_qual, "rdfs:subClassOf", self._resolve(_parts(base)))

    def _def_qualname(
        self, node: Node, prefix: str, class_qual: str | None
    ) -> tuple[str | None, str | None]:
        """(function qualname, class context for its body) for a function_definition."""
        name = _declared_name(node)
        if name is None:
            return None, class_qual
        parts = _parts(name)
        if not parts:
            return None, class_qual
        if len(parts) == 1:
            return f"{prefix}.{parts[0]}", class_qual
        # out-of-line member (Dog::bark) or namespace-qualified definition
        if parts[0] in self.classes:
            qualname = ".".join([self.classes[parts[0]], *parts[1:]])
        else:
            qualname = ".".join([prefix, *parts])
        owner = qualname.rsplit(".", 1)[0]
        return qualname, owner if owner in self.methods else class_qual

    def _call_target(self, fn: Node | None, class_qual: str | None) -> str | None:
        if fn is None:
            return None
        if fn.type == "identifier":
            name = text(fn)
            if name in self.classes:  # constructor call / functional cast
                return self.classes[name]
            if class_qual is not None and name in self.methods.get(class_qual, ()):
                return f"{class_qual}.{name}"
            return self.functions.get(name, name)
        if fn.type == "field_expression":  # x.f / x->f / this->f
            field = fn.child_by_field_name("field")
            receiver = fn.child_by_field_name("argument")
            if field is None:
                return None
            name = text(field)
            if class_qual is not None and (
                (receiver is not None and receiver.type == "this")
                or name in self.methods.get(class_qual, ())
            ):
                return f"{class_qual}.{name}"
            return name
        if fn.type == "qualified_identifier":  # A::f, std::move, ...
            return self._resolve(_parts(fn))
        return None


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    """Extract structural triples from one C/C++ file. Deduplicated, ordered.

    tree-sitter never raises on malformed input, so bad syntax degrades to
    partial extraction instead of failing.
    """
    root = parse("cpp", source)
    extractor = _Extractor(module)
    extractor.triples.append((module, "rdf:type", "Module"))
    extractor.collect(root, module, None)
    extractor.walk(root, module, None, None)
    return list(dict.fromkeys(extractor.triples))

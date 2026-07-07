"""Rust extractor (tree-sitter `rust` grammar).

Entity naming: module = root-relative path (base.path_module_name), so
``src/lib.rs`` → ``src.lib``. ``::`` paths become dots (``use crate::x::y``
→ imports ``crate.x.y``). ``impl Trait for Type`` maps to rdfs:subClassOf
(Type, rdfs:subClassOf, Trait); struct/enum/trait all get rdf:type Class.
Rust has no exceptions, so `raises` is not emitted.

Caveats / deliberate choices (deterministic, best effort):
  - Free functions are ``<module>.<fn>``; functions in ``impl``/``trait``
    blocks are ``<module>.<SelfType>.<fn>`` (SelfType = the impl's type
    field, unqualified); items inside inline ``mod x { .. }`` gain an ``x``
    segment. ``mod foo;`` file declarations are not resolved.
  - Calls: bare identifiers and ``a::b`` paths are resolved through
    same-file definitions and ``use`` bindings, else recorded verbatim
    (``String.from``). ``self.bark()`` inside an impl resolves to the
    enclosing SelfType's method. Method calls on receivers *other than*
    ``self`` are not recorded at all: without type inference the receiver's
    type is unknown, and a bare method name would collide across types.
  - Macro invocations (``println!`` etc.) are skipped: token trees are not
    expanded, so calls inside macro arguments are invisible.
  - ``crate``/``super``/``self`` path heads are kept as-is
    (``crate.utils.helper``); no cross-file module resolution is attempted.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from .base import emit, path_module_name
from .treesitter import parse, text

EXTENSIONS = {".rs"}

module_name = path_module_name

_TYPE_ITEMS = {"struct_item", "enum_item", "trait_item", "union_item"}
_PATH_HEADS = {"identifier", "crate", "super", "self", "metavariable"}


def _dotted(node: Node) -> str:
    """Render a (possibly scoped) path node as ``a.b.c`` (``::`` → ``.``)."""
    if node.type in ("scoped_identifier", "scoped_type_identifier"):
        path = node.child_by_field_name("path")
        name = node.child_by_field_name("name")
        head = _dotted(path) if path is not None else ""
        tail = text(name) if name is not None else ""
        return f"{head}.{tail}" if head and tail else head or tail
    return text(node)


def _type_name(node: Node | None) -> str | None:
    """Unqualified name of an impl's type node (``Foo<T>`` → ``Foo``), or None."""
    if node is None:
        return None
    if node.type in ("type_identifier", "identifier"):
        return text(node)
    if node.type in ("generic_type", "reference_type"):
        return _type_name(node.child_by_field_name("type"))
    if node.type == "scoped_type_identifier":
        return _type_name(node.child_by_field_name("name"))
    return None


def _use_targets(node: Node) -> list[tuple[str, str | None]]:
    """(dotted-path, bound-local-name-or-None) pairs for a use_declaration argument.

    Expands scoped lists, keeps ``as`` renames as (source-path, alias), and
    records wildcard imports as their prefix with no binding.
    """
    t = node.type
    if t in _PATH_HEADS:
        name = text(node)
        return [(name, name)]
    if t in ("scoped_identifier", "scoped_type_identifier"):
        dotted = _dotted(node)
        return [(dotted, dotted.rsplit(".", 1)[-1])]
    if t == "use_as_clause":
        path = node.child_by_field_name("path")
        alias = node.child_by_field_name("alias")
        if path is None:
            return []
        return [(_dotted(path), text(alias) if alias is not None else None)]
    if t == "use_wildcard":
        inner = next(iter(node.named_children), None)
        return [(_dotted(inner), None)] if inner is not None else []
    if t == "scoped_use_list":
        path = node.child_by_field_name("path")
        prefix = _dotted(path) if path is not None else ""
        lst = node.child_by_field_name("list")
        out: list[tuple[str, str | None]] = []
        for child in lst.named_children if lst is not None else []:
            for dotted, alias in _use_targets(child):
                if prefix and dotted == "self":  # use a::b::{self} → a.b
                    out.append((prefix, prefix.rsplit(".", 1)[-1]))
                else:
                    out.append((f"{prefix}.{dotted}" if prefix else dotted, alias))
        return out
    if t == "use_list":  # bare `use {a, b};`
        out = []
        for child in node.named_children:
            out.extend(_use_targets(child))
        return out
    return []


class _Extractor:
    def __init__(self, module: str):
        self.module = module
        self.triples: list[tuple[str, str, str]] = []
        # unqualified name → qualified name (free fns + type items, incl. inline mods)
        self.defs: dict[str, str] = {}
        # use-bound local name → dotted source path
        self.aliases: dict[str, str] = {}

    def _emit(self, s: str, p: str, o: str) -> None:
        emit(self.triples, s, p, o)

    def _qualify(self, name: str) -> str:
        """Resolve a dotted name's head through local defs and use bindings."""
        head, _, rest = name.partition(".")
        head = self.defs.get(head) or self.aliases.get(head) or head
        return f"{head}.{rest}" if rest else head

    # -- pass 1: names available for resolution ---------------------------------

    def collect(self, node: Node, prefix: str) -> None:
        for child in node.named_children:
            t = child.type
            if t == "function_item" or t in _TYPE_ITEMS:
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    self.defs[text(name_node)] = f"{prefix}.{text(name_node)}"
            elif t == "mod_item":
                name_node = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if name_node is not None and body is not None:
                    self.collect(body, f"{prefix}.{text(name_node)}")
            elif t == "use_declaration":
                arg = child.child_by_field_name("argument")
                for target, alias in _use_targets(arg) if arg is not None else []:
                    if alias:
                        self.aliases[alias] = target

    # -- pass 2: emit triples ----------------------------------------------------

    def _trait_ref(self, node: Node) -> str | None:
        """Resolve an impl's trait node: same-file trait / use binding / verbatim."""
        if node.type == "generic_type":
            inner = node.child_by_field_name("type")
            return self._trait_ref(inner) if inner is not None else None
        if node.type == "type_identifier":
            return self._qualify(text(node))
        if node.type == "scoped_type_identifier":
            return _dotted(node)
        return None

    def _call(self, node: Node, owner: str | None, func: str) -> None:
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "identifier":
            self._emit(func, "calls", self._qualify(text(fn)))
        elif fn.type == "scoped_identifier":
            self._emit(func, "calls", self._qualify(_dotted(fn)))
        elif fn.type == "field_expression":
            value = fn.child_by_field_name("value")
            field = fn.child_by_field_name("field")
            if value is not None and value.type == "self" and owner and field is not None:
                self._emit(func, "calls", f"{owner}.{text(field)}")

    def walk(self, node: Node, prefix: str, owner: str | None, func: str | None) -> None:
        """`prefix`: module + inline-mod path; `owner`: enclosing impl/trait
        qualname (methods hang off it); `func`: enclosing function qualname."""
        for child in node.named_children:
            t = child.type
            if t == "macro_invocation":
                continue  # token trees are not expanded
            if t == "use_declaration":
                arg = child.child_by_field_name("argument")
                for target, _ in _use_targets(arg) if arg is not None else []:
                    self._emit(self.module, "imports", target)
            elif t == "function_item":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                qualname = f"{owner or prefix}.{text(name_node)}"
                self._emit(qualname, "rdf:type", "Function")
                self._emit(qualname, "defined_in", self.module)
                body = child.child_by_field_name("body")
                if body is not None:
                    self.walk(body, prefix, owner, qualname)
            elif t in _TYPE_ITEMS:
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                qualname = f"{prefix}.{text(name_node)}"
                self._emit(qualname, "rdf:type", "Class")
                self._emit(qualname, "defined_in", self.module)
                body = child.child_by_field_name("body")
                if t == "trait_item" and body is not None:  # default methods
                    self.walk(body, prefix, qualname, func)
            elif t == "mod_item":
                name_node = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if name_node is not None and body is not None:
                    self.walk(body, f"{prefix}.{text(name_node)}", None, func)
            elif t == "impl_item":
                self_type = _type_name(child.child_by_field_name("type"))
                new_owner = f"{prefix}.{self_type}" if self_type else None
                trait_node = child.child_by_field_name("trait")
                if trait_node is not None and new_owner:
                    trait_ref = self._trait_ref(trait_node)
                    if trait_ref:
                        self._emit(new_owner, "rdfs:subClassOf", trait_ref)
                body = child.child_by_field_name("body")
                if body is not None:
                    self.walk(body, prefix, new_owner, func)
            elif t == "call_expression":
                if func:
                    self._call(child, owner, func)
                self.walk(child, prefix, owner, func)  # nested calls in arguments
            else:
                self.walk(child, prefix, owner, func)


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    """Extract structural triples from one Rust file. Deduplicated, ordered."""
    root = parse("rust", source)
    extractor = _Extractor(module)
    extractor.triples.append((module, "rdf:type", "Module"))
    extractor.collect(root, module)
    extractor.walk(root, module, None, None)
    return list(dict.fromkeys(extractor.triples))

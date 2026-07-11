"""TypeScript/JavaScript extractor (tree-sitter `typescript` / `tsx` grammars).

Entity naming: module = root-relative path (base.path_module_name), so
``src/utils/foo.ts`` → ``src.utils.foo``. Relative imports are resolved
against the importing file (``import x from './foo'`` in ``src/bar.ts`` →
imports ``src.foo``); bare specifiers are recorded as the package name.
``extends`` and ``implements`` both map to rdfs:subClassOf.

Caveats (inherent to static analysis, recorded as-is):
  - Only statically visible call targets are recorded; a call through a
    non-trivial receiver (``foo().bar()``) falls back to the bare method name.
  - ``const f = …`` function assignments count as definitions only at the
    top level of the module; class fields holding arrow functions and
    functions defined inside other functions are not walked as definitions.
  - ``throw`` records the thrown expression (``new X(…)`` → ``X``), so a
    re-thrown variable name may appear instead of an error class.
"""

from __future__ import annotations

from pathlib import Path

from .base import emit, path_module_name
from .treesitter import parse, text

try:  # only needed for type hints on Node-taking helpers
    from tree_sitter import Node
except ImportError:  # pragma: no cover
    Node = object  # type: ignore[assignment,misc]

EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

module_name = path_module_name

_GRAMMARS = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "tsx",  # tsx grammar parses plain JSX too
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

_CLASS_KINDS = {"class_declaration", "abstract_class_declaration", "interface_declaration"}
_FUNC_VALUE_KINDS = {"arrow_function", "function_expression", "generator_function"}
_DEF_KINDS = _CLASS_KINDS | {"function_declaration", "generator_function_declaration"}


def _string_value(node: Node | None) -> str | None:
    """The literal content of a `string` node, or None."""
    if node is None or node.type != "string":
        return None
    return "".join(text(c) for c in node.named_children if c.type == "string_fragment")


def _resolve_specifier(spec: str, module: str) -> str:
    """Import specifier → dotted module/package id.

    Relative specifiers resolve against the importing module's dotted path
    (``src.bar`` importing ``./foo`` → ``src.foo``; ``../x/y`` walks up).
    Bare specifiers become the package name (``@scope/pkg`` → ``scope.pkg``).
    """
    for ext in EXTENSIONS:
        if spec.endswith(ext):
            spec = spec[: -len(ext)]
            break
    if not spec.startswith("."):
        return spec.lstrip("@").replace("/", ".")
    parts = module.split(".")[:-1]  # directory of the importing file
    for seg in spec.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return ".".join(parts)


def _dotted(node: Node) -> str | None:
    """Render an identifier/member-expression chain as ``a.b.c``; None otherwise.

    ``this.x`` renders with a ``this`` head for _qualify to map to the class.
    """
    parts: list[str] = []
    while node.type in ("member_expression", "nested_type_identifier"):
        prop = node.child_by_field_name("property") or node.child_by_field_name("name")
        obj = node.child_by_field_name("object") or node.child_by_field_name("module")
        if prop is None or obj is None:
            return None
        parts.append(text(prop))
        node = obj
    if node.type in ("identifier", "type_identifier", "this"):
        parts.append("this" if node.type == "this" else text(node))
        return ".".join(reversed(parts))
    return None


def _heritage_type(node: Node) -> Node:
    """Unwrap ``generic_type`` (``Base<T>`` → ``Base``) to its name node."""
    if node.type == "generic_type":
        return node.child_by_field_name("name") or node
    return node


class _Extractor:
    def __init__(self, module: str):
        self.module = module
        self.triples: list[tuple[str, str, str]] = [(module, "rdf:type", "Module")]
        self.class_stack: list[str] = []  # qualified names of enclosing classes
        self.func_stack: list[str] = []  # qualified names of enclosing functions
        # name as written in this module → fully qualified name
        self.aliases: dict[str, str] = {}

    # -- helpers ---------------------------------------------------------------

    def _emit(self, s: str, p: str, o: str) -> None:
        emit(self.triples, s, p, o)

    def _qualify(self, name: str) -> str:
        """Resolve a dotted name through import aliases and local definitions."""
        head, _, rest = name.partition(".")
        if head == "this" and self.class_stack:
            head = self.class_stack[-1]
        elif head in self.aliases:
            head = self.aliases[head]
        return f"{head}.{rest}" if rest else head

    def prescan(self, root: Node) -> None:
        """Map top-level definition names to qualified names (hoisting)."""
        for stmt in root.named_children:
            decl = stmt.child_by_field_name("declaration") if stmt.type == "export_statement" else stmt
            if decl is None:
                continue
            if decl.type in _DEF_KINDS:
                name = decl.child_by_field_name("name")
                if name is not None:
                    self.aliases[text(name)] = f"{self.module}.{text(name)}"
            elif decl.type in ("lexical_declaration", "variable_declaration"):
                for declarator in decl.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    name = declarator.child_by_field_name("name")
                    value = declarator.child_by_field_name("value")
                    if name is None or value is None or name.type != "identifier":
                        continue
                    if value.type in _FUNC_VALUE_KINDS:
                        self.aliases[text(name)] = f"{self.module}.{text(name)}"
                    elif value.type == "call_expression":  # const x = require('…')
                        fn = value.child_by_field_name("function")
                        args = value.child_by_field_name("arguments")
                        if fn is not None and text(fn) == "require" and args is not None:
                            spec = next(
                                (_string_value(a) for a in args.named_children), None
                            )
                            if spec is not None:
                                self.aliases[text(name)] = _resolve_specifier(spec, self.module)

    # -- statement/expression handlers -----------------------------------------

    def _handle_import(self, node: Node) -> None:
        spec = _string_value(node.child_by_field_name("source"))
        if spec is None:
            return
        target = _resolve_specifier(spec, self.module)
        self._emit(self.module, "imports", target)
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for child in clause.named_children:
                if child.type == "identifier":  # default import
                    name = text(child)
                    if name.lower() == target.rpartition(".")[2].lower():
                        # `import chalk from 'chalk'`: binding stands for the module
                        self.aliases[name] = target
                    else:
                        self.aliases[name] = f"{target}.{name}"
                elif child.type == "namespace_import":  # import * as ns
                    for ident in child.named_children:
                        if ident.type == "identifier":
                            self.aliases[text(ident)] = target
                elif child.type == "named_imports":
                    for spec_node in child.named_children:
                        if spec_node.type != "import_specifier":
                            continue
                        name = spec_node.child_by_field_name("name")
                        alias = spec_node.child_by_field_name("alias") or name
                        if name is not None and alias is not None:
                            self.aliases[text(alias)] = f"{target}.{text(name)}"

    def _handle_require(self, node: Node) -> bool:
        """require('…') → imports triple. True if the call was a require."""
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or fn.type != "identifier" or text(fn) != "require" or args is None:
            return False
        spec = next((_string_value(a) for a in args.named_children), None)
        if spec is not None:
            self._emit(self.module, "imports", _resolve_specifier(spec, self.module))
        return True

    def _handle_function(self, node: Node, name: str) -> None:
        prefix = self.class_stack[-1] if self.class_stack else self.module
        qualname = f"{prefix}.{name}"
        self._emit(qualname, "rdf:type", "Function")
        self._emit(qualname, "defined_in", self.module)
        self.func_stack.append(qualname)
        self._walk_children(node)
        self.func_stack.pop()

    def _handle_class(self, node: Node) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        prefix = self.class_stack[-1] if self.class_stack else self.module
        qualname = f"{prefix}.{text(name)}"
        self._emit(qualname, "rdf:type", "Class")
        self._emit(qualname, "defined_in", self.module)
        for child in node.named_children:
            if child.type == "class_heritage":  # class: extends + implements
                for clause in child.named_children:
                    if clause.type in ("extends_clause", "implements_clause"):
                        bases = clause.named_children  # ts/tsx grammars
                    else:
                        bases = [clause]  # javascript grammar: bare expression
                    for base in bases:
                        dotted = _dotted(_heritage_type(base))
                        if dotted:
                            self._emit(qualname, "rdfs:subClassOf", self._qualify(dotted))
            elif child.type == "extends_type_clause":  # interface: extends
                for base in child.named_children:
                    dotted = _dotted(_heritage_type(base))
                    if dotted:
                        self._emit(qualname, "rdfs:subClassOf", self._qualify(dotted))
        self.class_stack.append(qualname)
        self._walk_children(node)
        self.class_stack.pop()

    def _handle_call(self, node: Node) -> None:
        if self._handle_require(node):
            pass
        elif self.func_stack:
            fn = node.child_by_field_name("function")
            if fn is not None:
                dotted = _dotted(fn)
                if dotted:
                    self._emit(self.func_stack[-1], "calls", self._qualify(dotted))
                elif fn.type == "member_expression":  # e.g. foo().bar() → "bar"
                    prop = fn.child_by_field_name("property")
                    if prop is not None:
                        self._emit(self.func_stack[-1], "calls", text(prop))
        self._walk_children(node)

    def _handle_new(self, node: Node) -> None:
        if self.func_stack:
            ctor = node.child_by_field_name("constructor")
            dotted = _dotted(ctor) if ctor is not None else None
            if dotted:
                self._emit(self.func_stack[-1], "calls", self._qualify(dotted))
        self._walk_children(node)

    def _handle_throw(self, node: Node) -> None:
        if self.func_stack:
            exc = next(iter(node.named_children), None)
            if exc is not None and exc.type == "new_expression":
                exc = exc.child_by_field_name("constructor")
            dotted = _dotted(exc) if exc is not None else None
            if dotted:
                self._emit(self.func_stack[-1], "raises", self._qualify(dotted))
        self._walk_children(node)

    def _handle_declarators(self, node: Node) -> None:
        """const f = () => …  /  const f = function … at the top level."""
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                self.walk(declarator)
                continue
            name = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            top_level = not self.func_stack and not self.class_stack
            if (
                top_level
                and name is not None
                and name.type == "identifier"
                and value is not None
                and value.type in _FUNC_VALUE_KINDS
            ):
                self._handle_function(value, text(name))
            else:
                self._walk_children(declarator)

    # -- walker ------------------------------------------------------------------

    def _walk_children(self, node: Node) -> None:
        for child in node.named_children:
            self.walk(child)

    def walk(self, node: Node) -> None:
        kind = node.type
        if kind == "import_statement":
            self._handle_import(node)
        elif kind == "export_statement":
            spec = _string_value(node.child_by_field_name("source"))
            if spec is not None:  # export … from '…' re-export
                self._emit(self.module, "imports", _resolve_specifier(spec, self.module))
            self._walk_children(node)
        elif kind in ("function_declaration", "generator_function_declaration", "method_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                self._handle_function(node, text(name))
            else:
                self._walk_children(node)
        elif kind in _CLASS_KINDS:
            self._handle_class(node)
        elif kind in ("lexical_declaration", "variable_declaration"):
            self._handle_declarators(node)
        elif kind == "call_expression":
            self._handle_call(node)
        elif kind == "new_expression":
            self._handle_new(node)
        elif kind == "throw_statement":
            self._handle_throw(node)
        else:
            self._walk_children(node)


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    """Extract structural triples from one TS/JS module. Deduplicated, ordered."""
    grammar = _GRAMMARS.get(path.suffix, "typescript") if path is not None else "typescript"
    root = parse(grammar, source)
    extractor = _Extractor(module)
    extractor.prescan(root)
    extractor.walk(root)
    return list(dict.fromkeys(extractor.triples))

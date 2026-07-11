"""Python extractor: stdlib ``ast``-based, the most precise of the family.

Caveats (inherent to static analysis, recorded as-is):
  - `ns:calls` covers only statically visible call targets; dynamic dispatch
    (duck typing, DI) cannot be resolved. Targets are resolved through import
    aliases and module-level definitions on a best-effort basis; unresolved
    names are recorded verbatim (e.g. ``json.dumps``).
  - `ns:raises` records the expression after ``raise`` (bare re-raises are
    skipped), so a re-raised variable name may appear instead of a class.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .base import emit

EXTENSIONS = {".py"}


def module_name_for(path: Path) -> str:
    """Dotted module name for a .py file, walking up through packages.

    Directories are included while they contain an ``__init__.py``, so
    ``src/nsai/kb.py`` becomes ``nsai.kb`` regardless of where the scan
    started. A file outside any package is just its stem.
    """
    path = path.resolve()
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    if parts[0] == "__init__":
        parts = parts[1:] or [path.parent.name]
    return ".".join(reversed(parts))


def _dotted(node: ast.expr) -> str | None:
    """Render a Name/Attribute chain as ``a.b.c``; None if anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Extractor(ast.NodeVisitor):
    def __init__(self, module: str, tree: ast.Module):
        self.module = module
        self.triples: list[tuple[str, str, str]] = []
        self.class_stack: list[str] = []  # qualified names of enclosing classes
        self.func_stack: list[str] = []  # qualified names of enclosing functions
        # name as written in this module → fully qualified name
        self.aliases: dict[str, str] = {}
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.aliases[stmt.name] = f"{module}.{stmt.name}"

    # -- helpers ---------------------------------------------------------------

    def _emit(self, s: str, p: str, o: str) -> None:
        emit(self.triples, s, p, o)

    def _qualify(self, name: str) -> str:
        """Resolve a dotted name through import aliases and local definitions."""
        head, _, rest = name.partition(".")
        if head in ("self", "cls") and self.class_stack:
            head = self.class_stack[-1]
        elif head in self.aliases:
            head = self.aliases[head]
        return f"{head}.{rest}" if rest else head

    def _handle_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        prefix = self.class_stack[-1] if self.class_stack else self.module
        qualname = f"{prefix}.{node.name}"
        self._emit(qualname, "rdf:type", "Function")
        self._emit(qualname, "defined_in", self.module)
        self.func_stack.append(qualname)
        self.generic_visit(node)
        self.func_stack.pop()

    # -- visitors --------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._emit(self.module, "imports", alias.name)
            self.aliases[alias.asname or alias.name.partition(".")[0]] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:  # relative import: resolve against this module's package
            base = self.module.split(".")[: -node.level]
            if not base:
                return
            target = ".".join(base + ([node.module] if node.module else []))
        else:
            target = node.module or ""
        if not target:
            return
        self._emit(self.module, "imports", target)
        for alias in node.names:
            if alias.name != "*":
                self.aliases[alias.asname or alias.name] = f"{target}.{alias.name}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prefix = self.class_stack[-1] if self.class_stack else self.module
        qualname = f"{prefix}.{node.name}"
        self._emit(qualname, "rdf:type", "Class")
        self._emit(qualname, "defined_in", self.module)
        for base in node.bases:
            if isinstance(base, ast.Subscript):  # e.g. Generic[T]
                base = base.value
            name = _dotted(base)
            if name:
                self._emit(qualname, "rdfs:subClassOf", self._qualify(name))
        self.class_stack.append(qualname)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_def(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_def(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.func_stack:
            name = _dotted(node.func)
            if name:
                self._emit(self.func_stack[-1], "calls", self._qualify(name))
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if self.func_stack and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = _dotted(exc)
            if name:
                self._emit(self.func_stack[-1], "raises", self._qualify(name))
        self.generic_visit(node)


def extract_module_triples(source: str, module: str) -> list[tuple[str, str, str]]:
    """Extract structural triples from one module's source. Deduplicated, ordered."""
    tree = ast.parse(source)
    extractor = _Extractor(module, tree)
    extractor.triples.append((module, "rdf:type", "Module"))
    extractor.visit(tree)
    return list(dict.fromkeys(extractor.triples))


def module_name(path: Path, root: Path) -> str:
    return module_name_for(path)


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    return extract_module_triples(source, module)

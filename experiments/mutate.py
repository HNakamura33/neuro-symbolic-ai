"""Deterministic AST-based mutation engine for experiment 4b.

Deliberately stdlib-`ast`-only instead of mutmut: the experiment needs an
explicit "boundary" operator class (docs/experiment-plan.md §4b) and grading
must be testable offline.

Operator classes (each mutant changes exactly ONE site):

- comparison   ``<`` <-> ``<=``, ``>`` <-> ``>=``, ``==`` <-> ``!=``   (boundary)
- constant     integer constant ``n`` -> ``n+1`` and ``n`` -> ``n-1``  (boundary)
- arithmetic   ``+`` <-> ``-``, ``*`` <-> ``//``
- boolean      ``and`` <-> ``or``

Only the body of the target function (``entry_point``) is mutated; helper
functions and module code are left intact but included in every mutant's
source. Docstrings and type annotations are never mutated (bool constants are
not "integers" here). Ordering is deterministic: depth-first source order,
with ``+1`` before ``-1`` at each constant site.

API:
    mutants(source, entry_point) -> list[Mutant]   # .source .op_class .site
    site_counts(source, entry_point) -> dict       # mutants per op_class
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

CMP_SWAPS = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
}
ARITH_SWAPS = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
}
BOOL_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}

_SYM = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=",
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//",
    ast.And: "and", ast.Or: "or",
}

#: op classes counted as boundary mutations (§4b: comparison swaps + ±1).
BOUNDARY_CLASSES = frozenset({"comparison", "constant"})

_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Site:
    """Position of the mutated node in the ORIGINAL source."""

    lineno: int
    col: int


@dataclass(frozen=True)
class Mutant:
    source: str        # full module source with exactly one site changed
    op_class: str      # comparison | constant | arithmetic | boolean
    site: Site
    description: str   # e.g. "<= -> <" or "100 -> 101"

    @property
    def boundary(self) -> bool:
        return self.op_class in BOUNDARY_CLASSES


def find_function(tree: ast.AST, entry_point: str) -> ast.FunctionDef:
    """The (first) function named ``entry_point`` anywhere in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_NODES) and node.name == entry_point:
            return node
    raise ValueError(f"no function named {entry_point!r} in source")


def _skip_ids(func: ast.AST) -> set[int]:
    """ids of every node inside a type annotation or a docstring."""
    skip: set[int] = set()

    def mark(sub: ast.AST) -> None:
        skip.update(id(n) for n in ast.walk(sub))

    for node in ast.walk(func):
        if isinstance(node, _FUNC_NODES):
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
                if arg is not None and arg.annotation is not None:
                    mark(arg.annotation)
            if node.returns is not None:
                mark(node.returns)
        elif isinstance(node, ast.AnnAssign):
            mark(node.annotation)
        if isinstance(node, (*_FUNC_NODES, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                mark(body[0])  # docstring
    return skip


def _dfs(node: ast.AST):
    """Depth-first, source-order traversal (deterministic)."""
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _dfs(child)


@dataclass
class _Atom:
    """One applicable mutation, addressable by its index in _collect()'s list
    (re-parsing the same source reproduces the identical enumeration)."""

    node: ast.AST
    kind: str          # cmp | arith | bool | const
    op_class: str
    description: str
    lineno: int
    col: int
    op_index: int = field(default=0)   # cmp: which op in a chained compare
    delta: int = field(default=0)      # const: +1 or -1


def _collect(func: ast.AST) -> list[_Atom]:
    skip = _skip_ids(func)
    atoms: list[_Atom] = []
    for node in _dfs(func):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                swap = CMP_SWAPS.get(type(op))
                if swap:
                    atoms.append(_Atom(
                        node, "cmp", "comparison",
                        f"{_SYM[type(op)]} -> {_SYM[swap]}",
                        node.lineno, node.col_offset, op_index=i,
                    ))
        elif isinstance(node, ast.BinOp):
            swap = ARITH_SWAPS.get(type(node.op))
            if swap:
                atoms.append(_Atom(
                    node, "arith", "arithmetic",
                    f"{_SYM[type(node.op)]} -> {_SYM[swap]}",
                    node.lineno, node.col_offset,
                ))
        elif isinstance(node, ast.BoolOp):
            swap = BOOL_SWAPS.get(type(node.op))
            if swap:
                atoms.append(_Atom(
                    node, "bool", "boolean",
                    f"{_SYM[type(node.op)]} -> {_SYM[swap]}",
                    node.lineno, node.col_offset,
                ))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            # type() not isinstance(): bool is an int subclass but True/False
            # are logic, not off-by-one material.
            for delta in (1, -1):
                atoms.append(_Atom(
                    node, "const", "constant",
                    f"{node.value} -> {node.value + delta}",
                    node.lineno, node.col_offset, delta=delta,
                ))
    return atoms


def _apply(atom: _Atom) -> None:
    node = atom.node
    if atom.kind == "cmp":
        node.ops[atom.op_index] = CMP_SWAPS[type(node.ops[atom.op_index])]()
    elif atom.kind == "arith":
        node.op = ARITH_SWAPS[type(node.op)]()
    elif atom.kind == "bool":
        node.op = BOOL_SWAPS[type(node.op)]()
    else:
        node.value = node.value + atom.delta


def mutants(source: str, entry_point: str) -> list[Mutant]:
    """All single-site mutants of ``entry_point`` within ``source``.

    Each Mutant.source is a full module (ast.unparse'd) with one change.
    Raises SyntaxError on unparseable source, ValueError on a missing
    entry point. Deterministic: same input, same list.
    """
    n = len(_collect(find_function(ast.parse(source), entry_point)))
    out: list[Mutant] = []
    for i in range(n):
        tree = ast.parse(source)  # fresh tree per mutant
        atoms = _collect(find_function(tree, entry_point))
        atom = atoms[i]
        _apply(atom)
        out.append(Mutant(
            source=ast.unparse(ast.fix_missing_locations(tree)) + "\n",
            op_class=atom.op_class,
            site=Site(atom.lineno, atom.col),
            description=atom.description,
        ))
    return out


def site_counts(source: str, entry_point: str) -> dict[str, int]:
    """Number of mutants per op class, without generating sources
    (cheap eligibility check for dataset sampling)."""
    counts: dict[str, int] = {}
    for atom in _collect(find_function(ast.parse(source), entry_point)):
        counts[atom.op_class] = counts.get(atom.op_class, 0) + 1
    return counts

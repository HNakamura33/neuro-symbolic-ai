"""Experiment 4d: extract the SWE-bench Verified boundary-bug subset.

Scans gold patches and keeps only tasks whose *entire* code change is a small
boundary / numeric-condition edit, classified by AST comparison of the
before/after snippets of each diff hunk (token-level fallback when a snippet
does not parse on its own).

Boundary-change classes
    comparison_operator  ``<`` <-> ``<=``, ``>`` <-> ``>=``, ``==`` <-> ``!=``
    constant_delta       small (|delta| <= 2) change to a numeric constant,
                         incl. ``x`` <-> ``x + 1`` rewrites
    minmax_guard         min()/max()/clamp insertion or fix, added/changed
                         boundary conditional guarding an index/length,
                         range-check widening (``a < b`` -> ``0 <= a < b``)
    index_date_arith     constant or arithmetic-operator tweak inside a
                         subscript or date/datetime arithmetic expression

A task qualifies iff ALL its code changes fall in these classes AND the patch
touches <= 2 non-test/doc files AND <= 10 changed lines (tests/docs excluded).

Usage:
    uv run python experiments/swebench_filter.py \
        --src data/swebench_verified.jsonl --out filtered.jsonl \
        [--report report.json]

``--src hf`` (or ``hf:<dataset>``) downloads the test split of
princeton-nlp/SWE-bench_Verified through the HuggingFace datasets-server
rows API (stdlib urllib only). ``--src`` may also be a .jsonl/.json file or
a directory containing such files.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# constants

CLASSES = ("comparison_operator", "constant_delta", "minmax_guard", "index_date_arith")

MAX_CODE_FILES = 2
MAX_CHANGED_LINES = 10
MAX_AST_DIFFS = 6
SMALL_DELTA = 2

BENIGN = "benign"  # sentinel: change is harmless (comment, docstring, str constant)

CMP_SWAPS = {
    frozenset({ast.Lt, ast.LtE}),
    frozenset({ast.Gt, ast.GtE}),
    frozenset({ast.Eq, ast.NotEq}),
}
CMP_TOKEN_SWAPS = {
    frozenset({"<", "<="}),
    frozenset({">", ">="}),
    frozenset({"==", "!="}),
}
ARITH_TOKENS = {"+", "-", "*", "//", "/", "%"}

DATE_HINTS = {
    "datetime", "date", "timedelta", "days", "day", "months", "month",
    "years", "year", "hours", "hour", "minutes", "minute", "seconds",
    "second", "microsecond", "microseconds", "weekday", "isoweekday",
    "toordinal", "fromordinal", "strftime", "strptime", "calendar",
}

GUARD_HINTS = {"len", "min", "max", "size", "length", "index", "count", "0"}

# names whose comparison reads as an index/length/range check
IDX_NAME_HINTS = {
    "i", "j", "k", "n", "num", "idx", "index", "indices", "pos", "position",
    "start", "stop", "end", "limit", "offset", "size", "length", "len",
    "count", "total", "width", "height", "depth", "min", "max",
}
SIZE_TRUTH_HINTS = {"size", "length", "count"}
BOUNDARY_CMP_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)

TOKEN_RE = re.compile(r"\d+\.\d+|\d+|\w+|<=|>=|==|!=|//|\*\*|->|\S")
NUM_RE = re.compile(r"^\d+(\.\d+)?$")

TEST_DIR_PARTS = {"tests", "test", "testing"}
DOC_EXTS = {".rst", ".md", ".txt", ".po", ".pot"}
DOC_DIR_PARTS = {"docs", "doc", "changes", "changelog", "news"}


# --------------------------------------------------------------------------
# unified-diff parsing

@dataclass
class Hunk:
    lines: list[tuple[str, str]] = field(default_factory=list)  # (tag, text)


@dataclass
class FileDiff:
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False


class PatchError(ValueError):
    pass


def parse_patch(text: str) -> list[FileDiff]:
    """Parse a git-style unified diff into per-file hunks."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_path = new_path = None

    def flush_file() -> None:
        nonlocal cur, hunk
        if cur is not None:
            files.append(cur)
        cur, hunk = None, None

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            flush_file()
            old_path = new_path = None
        elif line.startswith("Binary files"):
            flush_file()
            files.append(FileDiff(path="<binary>", binary=True))
        elif line.startswith("--- "):
            old_path = line[4:].split("\t")[0]
        elif line.startswith("+++ "):
            new_path = line[4:].split("\t")[0]
            path = new_path if new_path != "/dev/null" else (old_path or "")
            path = re.sub(r"^[ab]/", "", path)
            cur = FileDiff(path=path)
            hunk = None
        elif line.startswith("@@"):
            if cur is None:
                raise PatchError("hunk header before file header")
            if not re.match(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@", line):
                raise PatchError(f"bad hunk header: {line!r}")
            hunk = Hunk()
            cur.hunks.append(hunk)
        elif hunk is not None and line.startswith(("+", "-", " ")):
            hunk.lines.append((line[0], line[1:]))
        elif hunk is not None and line == "":
            hunk.lines.append((" ", ""))  # context blank line, no trailing space
        elif line.startswith("\\"):
            continue  # "\ No newline at end of file"
        # index/mode/rename/similarity lines: ignored
    flush_file()
    if not files:
        raise PatchError("no file diffs found in patch")
    return files


def change_blocks(hunk: Hunk) -> list[tuple[list[str], list[str]]]:
    """Group a hunk's -/+ runs into (before_lines, after_lines) blocks."""
    blocks: list[tuple[list[str], list[str]]] = []
    before: list[str] = []
    after: list[str] = []

    def flush() -> None:
        nonlocal before, after
        if before or after:
            blocks.append((before, after))
        before, after = [], []

    for tag, txt in hunk.lines:
        if tag == " ":
            flush()
        elif tag == "-":
            if after:  # '+' run already started: new block
                flush()
            before.append(txt)
        elif tag == "+":
            after.append(txt)
    flush()
    return blocks


def changed_line_count(fd: FileDiff) -> int:
    """Lines touched: per change block, max(#removed, #added)."""
    return sum(
        max(len(b), len(a)) for h in fd.hunks for b, a in change_blocks(h)
    )


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    base = parts[-1]
    return (
        any(p in TEST_DIR_PARTS for p in parts[:-1])
        or base.startswith("test_")
        or base.endswith("_test.py")
        or base == "conftest.py"
    )


def is_doc_path(path: str) -> bool:
    parts = path.lower().split("/")
    ext = Path(parts[-1]).suffix
    return ext in DOC_EXTS or any(p in DOC_DIR_PARTS for p in parts[:-1])


# --------------------------------------------------------------------------
# AST diffing

@dataclass
class Diff:
    kind: str            # "node" | "list"
    before: object       # AST node / list of nodes (or None)
    after: object
    parents: tuple       # enclosing before-side AST nodes, outermost first


def _key(x: object) -> str:
    return ast.dump(x) if isinstance(x, ast.AST) else repr(x)


def collect_diffs(a: object, b: object, parents: tuple = (), out: list | None = None) -> list[Diff]:
    if out is None:
        out = []
    if len(out) > MAX_AST_DIFFS:
        return out
    if isinstance(a, ast.AST) and isinstance(b, ast.AST):
        if type(a) is not type(b):
            out.append(Diff("node", a, b, parents))
            return out
        if isinstance(a, ast.Constant):
            if type(a.value) is not type(b.value) or a.value != b.value:
                out.append(Diff("node", a, b, parents))
            return out
        # Compare arity change (a < b  ->  0 <= a < b): report as one diff
        if isinstance(a, ast.Compare) and len(a.ops) != len(b.ops):
            out.append(Diff("node", a, b, parents))
            return out
        deeper = parents + (a,)
        for name in a._fields:
            va, vb = getattr(a, name, None), getattr(b, name, None)
            if isinstance(va, list) and isinstance(vb, list):
                _diff_lists(va, vb, deeper, out)
            elif isinstance(va, ast.AST) and isinstance(vb, ast.AST):
                collect_diffs(va, vb, deeper, out)
            elif isinstance(va, ast.AST) or isinstance(vb, ast.AST):
                out.append(Diff("node", va, vb, deeper))
            elif va != vb:  # identifier field (Name.id, Attribute.attr, ...)
                out.append(Diff("node", a, b, parents))
                break
        return out
    out.append(Diff("node", a, b, parents))
    return out


def _diff_lists(la: list, lb: list, parents: tuple, out: list) -> None:
    sm = difflib.SequenceMatcher(a=[_key(x) for x in la], b=[_key(x) for x in lb])
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for x, y in zip(la[i1:i2], lb[j1:j2]):
                collect_diffs(x, y, parents, out)
        else:
            out.append(Diff("list", la[i1:i2], lb[j1:j2], parents))


# --------------------------------------------------------------------------
# diff classification

def literal_num(node: object) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = literal_num(node.operand)
        if v is not None:
            return -v if isinstance(node.op, ast.USub) else v
    return None


def _is_minmax_call(n: object) -> bool:
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in {"min", "max"}
    )


def _index_or_date_context(parents: tuple) -> bool:
    if any(isinstance(p, ast.Subscript) for p in parents):
        return True
    scope = None
    for p in reversed(parents):
        if isinstance(p, ast.stmt):
            scope = p
            break
    if scope is None and parents:
        scope = parents[-1]
    if not isinstance(scope, ast.AST):
        return False
    for n in ast.walk(scope):
        if isinstance(n, ast.Name) and n.id in DATE_HINTS:
            return True
        if isinstance(n, ast.Attribute) and n.attr in DATE_HINTS:
            return True
    return False


def _is_none_check(node: object) -> bool:
    """``x is None`` / ``x is not None`` (possibly and/or/not-combined)."""
    if isinstance(node, ast.BoolOp):
        return all(_is_none_check(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_none_check(node.operand)
    return (
        isinstance(node, ast.Compare)
        and all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops)
        and any(
            isinstance(c, ast.Constant) and c.value is None
            for c in [node.left, *node.comparators]
        )
    )


def _idx_operand(node: object) -> bool:
    """Does the expression read as an index/length/size quantity?"""
    if not isinstance(node, ast.AST):
        return False
    if literal_num(node) is not None:
        return True
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in {"len", "min", "max", "abs"}:
            return True
        if isinstance(n, ast.Name) and n.id.lower() in IDX_NAME_HINTS:
            return True
        if isinstance(n, ast.Attribute) and n.attr.lower() in IDX_NAME_HINTS:
            return True
    return False


def _boundary_condition(node: object) -> bool:
    """A numeric/index boundary check: comparison over index-ish or numeric
    operands, an emptiness truth-test (``x.size``), or a min/max call."""
    if isinstance(node, ast.BoolOp):
        return all(_boundary_condition(v) or _is_none_check(v) for v in node.values) \
            and any(_boundary_condition(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _boundary_condition(node.operand)
    if _is_minmax_call(node):
        return True
    # emptiness truth-test: `x.size`, `count`, `len(x)`
    if isinstance(node, ast.Name) and node.id.lower() in SIZE_TRUTH_HINTS:
        return True
    if isinstance(node, ast.Attribute) and node.attr.lower() in SIZE_TRUTH_HINTS:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        return True
    if not isinstance(node, ast.Compare):
        return False
    if not all(isinstance(op, BOUNDARY_CMP_OPS) for op in node.ops):
        return False
    return any(_idx_operand(o) for o in [node.left, *node.comparators])


def _guardish_stmt(s: object) -> bool:
    """An inserted statement acceptable as a min/max/clamp/range-check fix."""
    if isinstance(s, ast.If):
        if _boundary_condition(s.test):
            return True
        # None-check wrapper around a nested boundary guard (precondition)
        if _is_none_check(s.test):
            return any(
                isinstance(n, ast.If) and _boundary_condition(n.test)
                for b in s.body for n in ast.walk(b)
            )
        return False
    if isinstance(s, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Return, ast.Expr)):
        return any(_is_minmax_call(n) for n in ast.walk(s))
    return False


def _strengthened_condition(a: object, b: object) -> str | None:
    """``cond`` -> ``cond and extra`` (b is a BoolOp containing a): class c iff
    the extra conjuncts are boundary checks (None-checks allowed alongside)."""
    if not isinstance(b, ast.BoolOp) or not isinstance(a, ast.AST) or isinstance(a, ast.BoolOp):
        return None
    da = ast.dump(a)
    added = [v for v in b.values if ast.dump(v) != da]
    if len(added) == len(b.values):  # `a` not among b's conjuncts
        return None
    if all(_boundary_condition(v) or _is_none_check(v) for v in added) and (
        _boundary_condition(a) or any(_boundary_condition(v) for v in added)
    ):
        return "minmax_guard"
    return None


def classify_node_diff(a: object, b: object, parents: tuple) -> str | None:
    ctx = _index_or_date_context(parents)
    # comparison operator swap
    if isinstance(a, ast.cmpop) and isinstance(b, ast.cmpop):
        return "comparison_operator" if frozenset({type(a), type(b)}) in CMP_SWAPS else None
    # arithmetic operator swap
    if isinstance(a, (ast.operator, ast.unaryop)) and isinstance(b, (ast.operator, ast.unaryop)):
        return "index_date_arith" if ctx else None
    # numeric constant tweak
    va, vb = literal_num(a), literal_num(b)
    if va is not None and vb is not None:
        if ctx:
            return "index_date_arith"
        return "constant_delta" if abs(va - vb) <= SMALL_DELTA else None
    # string/bytes constant change (message/docstring tweak): harmless
    if (
        isinstance(a, ast.Constant) and isinstance(b, ast.Constant)
        and type(a.value) is type(b.value) and isinstance(a.value, (str, bytes))
    ):
        return BENIGN
    # min()/max() wrap or unwrap of the same expression
    if _is_minmax_call(a) or _is_minmax_call(b):
        call, other = (a, b) if _is_minmax_call(a) else (b, a)
        if isinstance(other, ast.AST) and any(
            ast.dump(arg) == ast.dump(other) for arg in call.args
        ):
            return "minmax_guard"
    # min <-> max
    if isinstance(a, ast.Name) and isinstance(b, ast.Name) and {a.id, b.id} <= {"min", "max"}:
        return "minmax_guard"
    # x <-> x +/- small_const
    for expr, binop in ((a, b), (b, a)):
        if (
            isinstance(binop, ast.BinOp)
            and isinstance(binop.op, (ast.Add, ast.Sub))
            and isinstance(expr, ast.AST)
        ):
            for base, const in ((binop.left, binop.right), (binop.right, binop.left)):
                c = literal_num(const)
                if c is not None and abs(c) <= SMALL_DELTA and ast.dump(base) == ast.dump(expr):
                    return "index_date_arith" if ctx else "constant_delta"
    # range-check widening: a < b  ->  0 <= a < b (Compare arity change)
    if isinstance(a, ast.Compare) and isinstance(b, ast.Compare):
        if _boundary_condition(a) or _boundary_condition(b):
            return "minmax_guard"
        return None
    # condition strengthened with a boundary check: cond -> cond and extra
    c = _strengthened_condition(a, b)
    if c is not None:
        return c
    # date attribute tweak (.day <-> .days, ...)
    if (
        isinstance(a, ast.Attribute) and isinstance(b, ast.Attribute)
        and a.attr in DATE_HINTS and b.attr in DATE_HINTS
    ):
        return "index_date_arith"
    return None


def classify_list_diff(removed: list, added: list, parents: tuple) -> str | None:
    parent = parents[-1] if parents else None
    if removed and not added:
        if all(isinstance(s, ast.Pass) for s in removed):
            return BENIGN
        return None
    if not removed and added:
        if all(isinstance(s, ast.stmt) for s in added) and all(_guardish_stmt(s) for s in added):
            return "minmax_guard"
        if isinstance(parent, ast.BoolOp):
            kept = list(parent.values)  # before-side conjuncts (none removed)
            if all(_boundary_condition(v) or _is_none_check(v) for v in added) and (
                any(_boundary_condition(v) for v in added)
                or any(_boundary_condition(v) for v in kept)
            ):
                return "minmax_guard"
    return None


def classify_diffs(diffs: list[Diff]) -> set[str] | None:
    """Return the set of boundary classes, or None if any diff is unclassifiable."""
    classes: set[str] = set()
    for d in diffs:
        if d.kind == "node":
            c = classify_node_diff(d.before, d.after, d.parents)
        else:
            c = classify_list_diff(d.before, d.after, d.parents)
        if c is None:
            return None
        if c != BENIGN:
            classes.add(c)
    return classes


# --------------------------------------------------------------------------
# snippet reconstruction & parsing

def _strip_common_indent(before: list[str], after: list[str]) -> tuple[str, str]:
    all_lines = [l for l in before + after if l.strip()]
    if not all_lines:
        return "", ""
    m = min(len(l) - len(l.lstrip()) for l in all_lines)

    def cut(lines: list[str]) -> str:
        return "\n".join(l[m:] if l.strip() else "" for l in lines)

    return cut(before), cut(after)


def _parse_variants(src: str) -> list[ast.Module]:
    """Best-effort parses of a snippet (as-is; with a dangling block closed)."""
    out = []
    for candidate in (src, src + "\n    pass"):
        try:
            out.append(ast.parse(textwrap.dedent(candidate)))
        except (SyntaxError, ValueError, MemoryError):
            out.append(None)
    return out


# --------------------------------------------------------------------------
# token-level fallback

def _strip_comment(line: str) -> str:
    if "#" not in line:
        return line
    head = line.split("#", 1)[0]
    if head.count('"') % 2 == 0 and head.count("'") % 2 == 0:
        return head
    return line


def _tokens(line: str) -> list[str]:
    return TOKEN_RE.findall(_strip_comment(line))


def _bracket_depth(tokens: list[str], upto: int) -> int:
    return tokens[:upto].count("[") - tokens[:upto].count("]")


def _date_line(tokens: list[str]) -> bool:
    return any(t in DATE_HINTS for t in tokens)


def _has_minmax_call_tokens(toks: list[str]) -> bool:
    return any(
        t in {"min", "max"} and i + 1 < len(toks) and toks[i + 1] == "("
        for i, t in enumerate(toks)
    )


def _classify_guard_line(line: str, guard_seen: bool) -> set[str] | None:
    s = _strip_comment(line).strip()
    if not s:
        return set()
    toks = _tokens(s)
    if _has_minmax_call_tokens(toks):
        return {"minmax_guard"}
    if s.startswith(("if ", "elif ")) and any(
        t in {"<", "<=", ">", ">=", "==", "!="} for t in toks
    ) and any(t in GUARD_HINTS for t in toks):
        return {"minmax_guard"}
    if guard_seen and s.split()[0] in {"raise", "return", "continue", "break", "pass"}:
        return set()  # body of an already-recognized guard
    return None


def _classify_token_pair(ta: str, tb: str, depth: int, dated: bool) -> str | None:
    if frozenset({ta, tb}) in CMP_TOKEN_SWAPS:
        return "comparison_operator"
    if NUM_RE.match(ta) and NUM_RE.match(tb):
        if depth > 0 or dated:
            return "index_date_arith"
        return "constant_delta" if abs(float(ta) - float(tb)) <= SMALL_DELTA else None
    if ta in ARITH_TOKENS and tb in ARITH_TOKENS:
        return "index_date_arith" if (depth > 0 or dated) else None
    if {ta, tb} <= {"min", "max"}:
        return "minmax_guard"
    return None


def _classify_token_seg(seg: list[str], depth: int, dated: bool) -> str | None:
    """Classify a purely inserted (or deleted) token segment within a line."""
    if not seg:
        return BENIGN
    joined = "".join(seg)
    if _has_minmax_call_tokens(seg):
        return "minmax_guard"
    if all(t in ARITH_TOKENS or NUM_RE.match(t) for t in seg):
        if depth > 0 or dated:
            return "index_date_arith"
        nums = [float(t) for t in seg if NUM_RE.match(t)]
        if nums and all(n <= SMALL_DELTA for n in nums) and len(seg) <= 3:
            return "constant_delta"
    if not joined.strip():
        return BENIGN
    return None


def _classify_line_pair(lb: str, la: str) -> set[str] | None:
    ta, tb = _tokens(lb), _tokens(la)
    dated = _date_line(ta) or _date_line(tb)
    classes: set[str] = set()
    sm = difflib.SequenceMatcher(a=ta, b=tb)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        depth = _bracket_depth(ta, i1)
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for x, y in zip(ta[i1:i2], tb[j1:j2]):
                c = _classify_token_pair(x, y, depth, dated)
                if c is None:
                    return None
                classes.add(c)
        elif tag == "insert":
            c = _classify_token_seg(tb[j1:j2], depth, dated)
            if c is None:
                return None
            if c != BENIGN:
                classes.add(c)
        elif tag == "delete":
            c = _classify_token_seg(ta[i1:i2], depth, dated)
            if c is None:
                return None
            if c != BENIGN:
                classes.add(c)
        else:
            return None
    return classes


def token_classify_block(before: list[str], after: list[str]) -> set[str] | None:
    a = [l.strip() for l in before]
    b = [l.strip() for l in after]
    classes: set[str] = set()
    guard_seen = False
    sm = difflib.SequenceMatcher(a=a, b=b)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for lb, la in zip(before[i1:i2], after[j1:j2]):
                c = _classify_line_pair(lb, la)
                if c is None:
                    return None
                classes |= c
        elif tag == "insert" or (tag == "replace" and i1 == i2):
            for line in after[j1:j2]:
                c = _classify_guard_line(line, guard_seen)
                if c is None:
                    return None
                if "minmax_guard" in c:
                    guard_seen = True
                classes |= c
        elif tag == "delete":
            if not all(not _strip_comment(l).strip() for l in before[i1:i2]):
                return None
        else:
            return None
    return classes


# --------------------------------------------------------------------------
# block / instance classification

def classify_block(before: list[str], after: list[str]) -> set[str] | None:
    """Classes for one change block; empty set = benign; None = unclassifiable."""
    src_b, src_a = _strip_common_indent(before, after)
    if not src_b.strip() and not src_a.strip():
        return set()
    # pure insertion
    if not src_b.strip():
        for tree in _parse_variants(src_a):
            if tree is not None:
                if tree.body and all(_guardish_stmt(s) for s in tree.body):
                    return {"minmax_guard"}
                if not tree.body:
                    return set()  # comments only
                break
        return token_classify_block([], after)
    # pure deletion: only comments/blank may go
    if not src_a.strip():
        for tree in _parse_variants(src_b):
            if tree is not None and not tree.body:
                return set()
        if all(not _strip_comment(l).strip() for l in before):
            return set()
        return None
    # replacement: AST comparison first
    vb, va = _parse_variants(src_b), _parse_variants(src_a)
    for i, j in ((0, 0), (1, 1), (0, 1), (1, 0)):
        if vb[i] is not None and va[j] is not None:
            diffs = collect_diffs(vb[i], va[j])
            if len(diffs) > MAX_AST_DIFFS:
                return None
            return classify_diffs(diffs)
    return token_classify_block(before, after)


def classify_instance(
    inst: dict,
    max_files: int = MAX_CODE_FILES,
    max_lines: int = MAX_CHANGED_LINES,
) -> tuple[list[str] | None, str | None]:
    """Return (filter_classes, None) if qualifying else (None, reject_reason)."""
    patch = inst.get("patch") or ""
    try:
        files = parse_patch(patch)
    except PatchError:
        return None, "unparseable_patch"
    active = [f for f in files if f.hunks or f.binary]
    code = [f for f in active if not (is_test_path(f.path) or is_doc_path(f.path))]
    if not code:
        return None, "test_or_doc_only"
    if any(f.binary or not f.path.endswith(".py") for f in code):
        return None, "non_py_code_change"
    if len(code) > max_files:
        return None, "too_many_files"
    if sum(changed_line_count(f) for f in code) > max_lines:
        return None, "too_many_lines"
    classes: set[str] = set()
    for f in code:
        for h in f.hunks:
            for b, a in change_blocks(h):
                c = classify_block(b, a)
                if c is None:
                    return None, "unclassified_change"
                classes |= c
    if not classes:
        return None, "no_boundary_change"
    return sorted(classes), None


# --------------------------------------------------------------------------
# dataset loading

HF_DEFAULT = "princeton-nlp/SWE-bench_Verified"
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"


def fetch_hf(dataset: str = HF_DEFAULT, split: str = "test") -> list[dict]:
    rows: list[dict] = []
    offset, total = 0, None
    while total is None or offset < total:
        qs = urllib.parse.urlencode(
            {"dataset": dataset, "config": "default", "split": split,
             "offset": offset, "length": 100}
        )
        req = urllib.request.Request(
            f"{HF_ROWS_URL}?{qs}", headers={"User-Agent": "nsai-exp4d/0.1"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        batch = [r["row"] for r in data.get("rows", [])]
        if not batch:
            break
        rows.extend(batch)
        total = data.get("num_rows_total", len(rows))
        offset += len(batch)
    return rows


def load_instances(src: str) -> tuple[list[dict], str]:
    """Load instances; returns (instances, source_description)."""
    if src == "hf" or src.startswith("hf:"):
        dataset = src[3:] if src.startswith("hf:") and src[3:] else HF_DEFAULT
        return fetch_hf(dataset), f"huggingface datasets-server: {dataset} [test]"
    path = Path(src)
    if path.is_dir():
        paths = sorted(list(path.glob("*.jsonl")) + list(path.glob("*.json")))
        if not paths:
            raise FileNotFoundError(f"no .jsonl/.json files in {path}")
    elif path.is_file():
        paths = [path]
    else:
        raise FileNotFoundError(src)
    out: list[dict] = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".json":
            loaded = json.loads(text)
            out.extend(loaded if isinstance(loaded, list) else [loaded])
        else:
            out.extend(json.loads(l) for l in text.splitlines() if l.strip())
    return out, str(path)


# --------------------------------------------------------------------------
# CLI

def run_filter(
    instances: list[dict],
    max_files: int = MAX_CODE_FILES,
    max_lines: int = MAX_CHANGED_LINES,
) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    hits = {c: 0 for c in CLASSES}
    excluded: dict[str, int] = {}
    for inst in instances:
        classes, reason = classify_instance(inst, max_files=max_files, max_lines=max_lines)
        if classes is None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        rec = dict(inst)
        rec["filter_classes"] = classes
        kept.append(rec)
        for c in classes:
            hits[c] += 1
    report = {
        "total_scanned": len(instances),
        "qualified": len(kept),
        "hits_per_class": hits,
        "excluded": dict(sorted(excluded.items())),
        "filter_criteria": {
            "classes": list(CLASSES),
            "max_code_files": max_files,
            "max_changed_lines": max_lines,
            "changed_line_metric": "sum over change blocks of max(#removed, #added), tests/docs excluded",
            "small_constant_delta": SMALL_DELTA,
            "rule": "ALL code changes must fall in the boundary classes",
            "benign_changes": "comments, whitespace, str/bytes constant tweaks "
                              "(do not count as boundary hits; alone they do not qualify)",
        },
        "qualified_instance_ids": [r.get("instance_id") for r in kept],
    }
    return kept, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True,
                    help=".jsonl/.json file, directory of such files, or 'hf'/'hf:<dataset>' to download")
    ap.add_argument("--out", required=True, help="output filtered.jsonl")
    ap.add_argument("--report", default=None, help="optional report.json path")
    ap.add_argument("--max-files", type=int, default=MAX_CODE_FILES,
                    help=f"max non-test/doc files touched (default {MAX_CODE_FILES})")
    ap.add_argument("--max-lines", type=int, default=MAX_CHANGED_LINES,
                    help=f"max changed lines excluding tests/docs (default {MAX_CHANGED_LINES})")
    args = ap.parse_args(argv)

    instances, source = load_instances(args.src)
    kept, report = run_filter(instances, max_files=args.max_files, max_lines=args.max_lines)
    report["source"] = source

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in kept:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"source: {source}")
    print(f"scanned {report['total_scanned']}, qualified {report['qualified']}")
    print(f"hits per class: {report['hits_per_class']}")
    print(f"excluded: {report['excluded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

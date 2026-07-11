"""Offline fixture tests for experiments/swebench_filter.py (experiment 4d).

All patches are handwritten in the SWE-bench Verified instance schema
{instance_id, repo, patch, ...}; no network or dataset download involved.
"""

import json
from pathlib import Path

import pytest

from experiments.swebench_filter import (
    change_blocks,
    classify_block,
    classify_instance,
    is_doc_path,
    is_test_path,
    main,
    parse_patch,
    run_filter,
)


def mkpatch(path: str, hunk_header: str, body: list[str]) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"{hunk_header}\n" + "\n".join(body) + "\n"
    )


def inst(patch: str, iid: str = "repo__proj-1") -> dict:
    return {"instance_id": iid, "repo": "org/proj", "patch": patch}


# -- boundary class fixtures ---------------------------------------------------------

COMPARISON_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -10,7 +10,7 @@ def f(x, n):",
    [
        "     for i in range(n):",
        "-        if i < n - 1:",
        "+        if i <= n - 1:",
        "             yield i",
    ],
)

CONSTANT_DELTA_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -20,5 +20,5 @@ def g(count):",
    [
        " def g(count):",
        "-    total = count + 2",
        "+    total = count + 3",
        "     return total",
    ],
)

# off-by-one written as a structural rewrite: x  ->  x + 1
OFFBYONE_REWRITE_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -5,4 +5,4 @@ def h(n):",
    [
        " def h(n):",
        "-    return range(n)",
        "+    return range(n + 1)",
    ],
)

MINMAX_CHANGE_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -30,4 +30,4 @@ def page(start, size, length):",
    [
        " def page(start, size, length):",
        "-    end = start + size",
        "+    end = min(start + size, length)",
        "     return end",
    ],
)

GUARD_INSERTION_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -40,4 +40,6 @@ def pick(items, index):",
    [
        " def pick(items, index):",
        "+    if index >= len(items):",
        "+        raise IndexError(index)",
        "     return items[index]",
    ],
)

INDEX_ARITH_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -50,4 +50,4 @@ def last(series, pos):",
    [
        " def last(series, pos):",
        "-    return series[pos - 1]",
        "+    return series[pos]",
    ],
)

DATE_ARITH_PATCH = mkpatch(
    "pkg/mod.py",
    "@@ -60,4 +60,4 @@ def week_end(start):",
    [
        " def week_end(start):",
        "-    return start + timedelta(days=6)",
        "+    return start + timedelta(days=7)",
    ],
)


@pytest.mark.parametrize(
    "patch,expected",
    [
        (COMPARISON_PATCH, ["comparison_operator"]),
        (CONSTANT_DELTA_PATCH, ["constant_delta"]),
        (OFFBYONE_REWRITE_PATCH, ["constant_delta"]),
        (MINMAX_CHANGE_PATCH, ["minmax_guard"]),
        (GUARD_INSERTION_PATCH, ["minmax_guard"]),
        (INDEX_ARITH_PATCH, ["index_date_arith"]),
        (DATE_ARITH_PATCH, ["index_date_arith"]),
    ],
    ids=[
        "comparison-op",
        "constant-delta",
        "offbyone-rewrite",
        "minmax-wrap",
        "guard-insertion",
        "index-arith",
        "date-arith",
    ],
)
def test_boundary_classes_detected(patch, expected):
    classes, reason = classify_instance(inst(patch))
    assert reason is None
    assert classes == expected


def test_multi_class_patch():
    body = mkpatch(
        "pkg/a.py",
        "@@ -10,4 +10,4 @@ def f(i, n):",
        [
            " def f(i, n):",
            "-    if i < n:",
            "+    if i <= n:",
            "         return i",
        ],
    ) + mkpatch(
        "pkg/b.py",
        "@@ -5,4 +5,4 @@ def g(xs, k):",
        [
            " def g(xs, k):",
            "-    return xs[k + 1]",
            "+    return xs[k]",
        ],
    )
    classes, reason = classify_instance(inst(body))
    assert reason is None
    assert classes == ["comparison_operator", "index_date_arith"]


# -- rejections ----------------------------------------------------------------------

def test_reject_large_patch_by_lines():
    body = [" def f(a):"]
    for i in range(12):
        body.append(f"-    x{i} = a < {i}")
        body.append(f"+    x{i} = a <= {i}")
    patch = mkpatch("pkg/mod.py", "@@ -1,14 +1,14 @@ def f(a):", body)
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "too_many_lines"


def test_reject_too_many_files():
    patch = "".join(
        mkpatch(
            f"pkg/m{i}.py",
            "@@ -1,3 +1,3 @@",
            [" def f(i, n):", "-    return i < n", "+    return i <= n"],
        )
        for i in range(3)
    )
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "too_many_files"


def test_reject_non_boundary_logic_change():
    patch = mkpatch(
        "pkg/mod.py",
        "@@ -8,4 +8,4 @@ def f(x):",
        [
            " def f(x):",
            "-    result = compute(x)",
            "+    result = process(x, strict=True)",
        ],
    )
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "unclassified_change"


def test_reject_test_file_only_patch():
    patch = mkpatch(
        "tests/test_mod.py",
        "@@ -1,3 +1,3 @@",
        [" def test_f():", "-    assert f(1) < 2", "+    assert f(1) <= 2"],
    )
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "test_or_doc_only"


def test_reject_non_python_code_change():
    patch = mkpatch(
        "src/core.c",
        "@@ -1,3 +1,3 @@",
        [" int f(int i, int n) {", "-    return i < n;", "+    return i <= n;"],
    )
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "non_py_code_change"


def test_reject_boolean_flip():
    patch = mkpatch(
        "pkg/mod.py",
        "@@ -1,3 +1,3 @@",
        [" def f():", "-    strict = True", "+    strict = False"],
    )
    classes, reason = classify_instance(inst(patch))
    assert classes is None
    assert reason == "unclassified_change"


def test_reject_unparseable_patch():
    classes, reason = classify_instance(inst("this is not a diff"))
    assert classes is None
    assert reason == "unparseable_patch"


# -- diff parsing edge cases ---------------------------------------------------------

def test_parse_patch_multiple_hunks_and_files():
    patch = (
        mkpatch(
            "pkg/a.py",
            "@@ -1,3 +1,3 @@",
            [" x = 1", "-y = 2", "+y = 3"],
        )
        + "@@ -10,3 +10,4 @@ def f():\n"
        + " def f():\n+    return None\n \n"
        + mkpatch("pkg/b.py", "@@ -1,2 +1,1 @@", [" import os", "-import sys"])
    )
    files = parse_patch(patch)
    assert [f.path for f in files] == ["pkg/a.py", "pkg/b.py"]
    assert len(files[0].hunks) == 2
    assert len(files[1].hunks) == 1
    # blocks: replacement, pure insertion, pure deletion
    assert change_blocks(files[0].hunks[0]) == [(["y = 2"], ["y = 3"])]
    assert change_blocks(files[0].hunks[1]) == [([], ["    return None"])]
    assert change_blocks(files[1].hunks[0]) == [(["import sys"], [])]


def test_parse_patch_new_and_deleted_file_paths():
    patch = (
        "diff --git a/pkg/new.py b/pkg/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/pkg/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def f():\n"
        "+    return 1\n"
        "diff --git a/pkg/old.py b/pkg/old.py\n"
        "deleted file mode 100644\n"
        "--- a/pkg/old.py\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-x = 1\n"
        "\\ No newline at end of file\n"
    )
    files = parse_patch(patch)
    assert [f.path for f in files] == ["pkg/new.py", "pkg/old.py"]
    assert change_blocks(files[0].hunks[0]) == [([], ["def f():", "    return 1"])]
    assert change_blocks(files[1].hunks[0]) == [(["x = 1"], [])]


def test_hunk_with_interleaved_blocks():
    hunk_patch = mkpatch(
        "pkg/mod.py",
        "@@ -1,8 +1,8 @@",
        [
            " a = 1",
            "-b = 2",
            "+b = 3",
            " c = 4",
            "-d = 5",
            "+d = 6",
            " e = 7",
        ],
    )
    files = parse_patch(hunk_patch)
    blocks = change_blocks(files[0].hunks[0])
    assert blocks == [(["b = 2"], ["b = 3"]), (["d = 5"], ["d = 6"])]


# -- path classification -------------------------------------------------------------

def test_path_classifiers():
    assert is_test_path("sympy/utilities/tests/test_lambdify.py")
    assert is_test_path("test_foo.py")
    assert is_test_path("pkg/conftest.py")
    assert not is_test_path("pkg/latest.py")
    assert is_doc_path("docs/usage.rst")
    assert is_doc_path("CHANGES/1234.bugfix.rst")
    assert is_doc_path("README.md")
    assert not is_doc_path("pkg/mod.py")


# -- block-level details -------------------------------------------------------------

def test_comment_only_change_is_benign():
    assert classify_block(["# old comment"], ["# new comment"]) == set()


def test_whitespace_only_change_is_benign():
    assert classify_block(["x = f( a )"], ["x = f(a)"]) == set()


def test_string_message_tweak_alongside_boundary_fix():
    patch = mkpatch(
        "pkg/mod.py",
        "@@ -1,6 +1,6 @@",
        [
            " def f(n):",
            "-    if n < 0:",
            '-        raise ValueError("bad n")',
            "+    if n <= 0:",
            '+        raise ValueError("n must be positive")',
            "     return n",
        ],
    )
    classes, reason = classify_instance(inst(patch))
    assert reason is None
    assert classes == ["comparison_operator"]


def test_token_fallback_on_unparseable_snippet():
    # `else:` branch does not parse standalone -> token-level path
    classes = classify_block(
        ["else:", "    limit = size - 1"],
        ["else:", "    limit = size - 2"],
    )
    assert classes == {"constant_delta"}


def test_range_check_widening_is_minmax_guard():
    classes = classify_block(["if i < n:"], ["if 0 <= i < n:"])
    assert classes == {"minmax_guard"}


# -- CLI end-to-end -------------------------------------------------------------------

def test_main_end_to_end(tmp_path: Path):
    src = tmp_path / "verified.jsonl"
    rows = [
        inst(COMPARISON_PATCH, "proj__a-1"),
        inst(DATE_ARITH_PATCH, "proj__b-2"),
        inst(
            mkpatch(
                "pkg/mod.py",
                "@@ -8,4 +8,4 @@",
                [" def f(x):", "-    return compute(x)", "+    return process(x)"],
            ),
            "proj__c-3",
        ),
    ]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "filtered.jsonl"
    report_path = tmp_path / "report.json"

    rc = main(["--src", str(src), "--out", str(out), "--report", str(report_path)])
    assert rc == 0

    kept = [json.loads(l) for l in out.read_text().splitlines()]
    assert [r["instance_id"] for r in kept] == ["proj__a-1", "proj__b-2"]
    assert kept[0]["filter_classes"] == ["comparison_operator"]
    assert kept[1]["filter_classes"] == ["index_date_arith"]
    # original fields preserved verbatim
    assert kept[0]["repo"] == "org/proj"
    assert kept[0]["patch"] == COMPARISON_PATCH

    report = json.loads(report_path.read_text())
    assert report["total_scanned"] == 3
    assert report["qualified"] == 2
    assert report["hits_per_class"]["comparison_operator"] == 1
    assert report["hits_per_class"]["index_date_arith"] == 1
    assert report["excluded"] == {"unclassified_change": 1}
    assert report["filter_criteria"]["max_code_files"] == 2
    assert report["qualified_instance_ids"] == ["proj__a-1", "proj__b-2"]


def test_run_filter_counts():
    kept, report = run_filter([inst(GUARD_INSERTION_PATCH), inst(MINMAX_CHANGE_PATCH)])
    assert report["qualified"] == 2
    assert report["hits_per_class"]["minmax_guard"] == 2
    assert all(r["filter_classes"] == ["minmax_guard"] for r in kept)

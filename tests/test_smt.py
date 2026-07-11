from nsai.smt import smt_verify


def test_proved():
    r = smt_verify(
        variables={"x": "int", "y": "int"},
        assumptions=["x > 5", "y == 2 * x"],
        goal="y > 10",
    )
    assert r["verdict"] == "proved"


def test_refuted_with_counterexample():
    r = smt_verify(
        variables={"x": "int"},
        assumptions=["x > 0"],
        goal="x > 5",
    )
    assert r["verdict"] == "refuted"
    assert "x" in r["counterexample"]


def test_consistency_check():
    ok = smt_verify(variables={"a": "bool"}, assumptions=["a"], goal=None)
    assert ok["verdict"] == "consistent"

    bad = smt_verify(
        variables={"x": "int"}, assumptions=["x > 1", "x < 0"], goal=None
    )
    assert bad["verdict"] == "inconsistent"


def test_connectives():
    r = smt_verify(
        variables={"a": "bool", "b": "bool"},
        assumptions=["Implies(a, b)", "a"],
        goal="b",
    )
    assert r["verdict"] == "proved"


def test_unknown_name_rejected():
    r = smt_verify(variables={"x": "int"}, assumptions=["x == evil"], goal=None)
    assert "error" in r


def test_bad_type_rejected():
    r = smt_verify(variables={"x": "string"}, assumptions=[], goal=None)
    assert "error" in r


def test_timeout_returns_unknown_with_guidance(monkeypatch):
    # Factoring a 54-digit prime: nlsat cannot decide this quickly, so the
    # solver must hit the time limit and come back instead of wedging the
    # in-process MCP server (observed live: 3.5h at 100% CPU without it).
    import nsai.smt as smt_mod

    monkeypatch.setattr(smt_mod, "TIMEOUT_MS", 1000)
    r = smt_mod.smt_verify(
        variables={"x": "int", "y": "int"},
        assumptions=[
            "x > 1", "y > 1",
            "x * y == 1000000000000000000000000000000000000000000000000000061",
        ],
        goal=None,
    )
    assert r["verdict"] == "unknown"
    assert "time limit" in r["detail"]

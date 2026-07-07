from nsai.csp import solve_csp


def test_basic_scheduling():
    result = solve_csp(
        variables={"a": [1, 2, 3], "b": [1, 2, 3]},
        constraints=["a < b"],
    )
    assert result["count"] == 3
    assert {"a": 1, "b": 2} in result["solutions"]


def test_all_different():
    result = solve_csp(
        variables={"x": [1, 2], "y": [1, 2]},
        constraints=[],
        all_different=True,
    )
    assert result["count"] == 2


def test_unsatisfiable():
    result = solve_csp(
        variables={"x": [1], "y": [1]},
        constraints=["x != y"],
    )
    assert result["count"] == 0


def test_unknown_name_rejected():
    result = solve_csp(variables={"x": [1]}, constraints=["x == evil()"])
    assert "error" in result


def test_empty_domain_rejected():
    result = solve_csp(variables={"x": []}, constraints=[])
    assert "error" in result

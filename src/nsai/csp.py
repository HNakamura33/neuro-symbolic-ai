"""Constraint-satisfaction solver — the symbolic engine for planning tasks.

The LLM formalizes a scheduling/assignment problem into variables, domains,
and constraint expressions; this module solves it exactly.
"""

from __future__ import annotations

from constraint import AllDifferentConstraint, Problem

# Names usable inside constraint expressions (deliberately no builtins).
_SAFE_GLOBALS = {"__builtins__": {}, "abs": abs, "min": min, "max": max, "len": len}

MAX_SOLUTIONS = 20


def solve_csp(
    variables: dict[str, list],
    constraints: list[str],
    all_different: bool = False,
) -> dict:
    """Solve a constraint-satisfaction problem.

    Args:
        variables: mapping of variable name -> list of possible values.
        constraints: python boolean expressions over the variable names,
            e.g. "alice != bob", "meeting_a + 1 <= meeting_b".
        all_different: if True, all variables must take distinct values.

    Returns:
        {"solutions": [...], "count": n, "truncated": bool}
    """
    problem = Problem()
    for name, domain in variables.items():
        if not domain:
            return {"error": f"variable '{name}' has an empty domain"}
        problem.addVariable(name, domain)

    if all_different:
        problem.addConstraint(AllDifferentConstraint())

    var_names = list(variables)
    for expr in constraints:
        code = compile(expr, "<constraint>", "eval")
        used = [n for n in code.co_names if n in variables]
        unknown = [n for n in code.co_names if n not in variables and n not in _SAFE_GLOBALS]
        if unknown:
            return {"error": f"constraint {expr!r} references unknown names: {unknown}"}

        def make_check(code=code, used=used):
            def check(*values):
                local = dict(zip(used, values))
                return bool(eval(code, _SAFE_GLOBALS, local))  # noqa: S307 — sandboxed names

            return check

        problem.addConstraint(make_check(), used or var_names)

    solutions = problem.getSolutions()
    truncated = len(solutions) > MAX_SOLUTIONS
    return {
        "solutions": solutions[:MAX_SOLUTIONS],
        "count": len(solutions),
        "truncated": truncated,
    }

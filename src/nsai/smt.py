"""Z3 wrapper — exact verification of numeric/boolean claims.

The LLM formalizes a claim into typed variables, assumptions, and a goal;
Z3 either proves the goal follows from the assumptions or produces a
counterexample. Complements the KB (facts) and CSP (finite domains) with
unbounded arithmetic.
"""

from __future__ import annotations

import z3

_TYPES = {"int": z3.Int, "real": z3.Real, "bool": z3.Bool}

#: Hard cap on solver time. Z3's nonlinear arithmetic (nlsat) can run for
#: hours on innocuous-looking goals; the MCP server runs in-process, so an
#: unbounded check() wedges the whole agent session (observed live: two
#: experiment streams spun at 100% CPU for 3.5h inside nlsat::explain).
#: On timeout check() returns unknown and the agent is told to reformulate.
TIMEOUT_MS = 30_000

# Only z3 connectives are exposed; expressions use python operators
# (<, <=, ==, +, *, ...) on z3 terms, plus And/Or/Not/Implies.
_SAFE_GLOBALS = {
    "__builtins__": {},
    "And": z3.And,
    "Or": z3.Or,
    "Not": z3.Not,
    "Implies": z3.Implies,
    "If": z3.If,
}


def _parse(expr: str, env: dict) -> z3.ExprRef:
    code = compile(expr, "<smt>", "eval")
    unknown = [n for n in code.co_names if n not in env and n not in _SAFE_GLOBALS]
    if unknown:
        raise ValueError(f"expression {expr!r} references unknown names: {unknown}")
    return eval(code, _SAFE_GLOBALS, env)  # noqa: S307 — names are sandboxed


def smt_verify(
    variables: dict[str, str],
    assumptions: list[str],
    goal: str | None = None,
) -> dict:
    """Check whether `goal` follows from `assumptions` over typed variables.

    With a goal:     unsat(assumptions ∧ ¬goal) → proved;
                     sat → counterexample (model returned).
    Without a goal:  consistency check of the assumptions alone.
    """
    try:
        env = {name: _TYPES[t.lower()](name) for name, t in variables.items()}
    except KeyError as e:
        return {"error": f"unknown type {e} (use int | real | bool)"}

    solver = z3.Solver()
    solver.set("timeout", TIMEOUT_MS)
    try:
        for a in assumptions:
            solver.add(_parse(a, env))
        goal_expr = _parse(goal, env) if goal else None
    except (ValueError, SyntaxError, z3.Z3Exception) as e:
        return {"error": str(e)}

    if goal_expr is None:
        status = solver.check()
        if status == z3.sat:
            model = solver.model()
            return {
                "verdict": "consistent",
                "model": {d.name(): str(model[d]) for d in model.decls()},
            }
        if status == z3.unsat:
            return {"verdict": "inconsistent", "detail": "The assumptions contradict each other."}
        return {"verdict": "unknown", "detail": _unknown_detail(solver)}

    solver.add(z3.Not(goal_expr))
    status = solver.check()
    if status == z3.unsat:
        return {"verdict": "proved", "detail": "The goal follows from the assumptions."}
    if status == z3.sat:
        model = solver.model()
        return {
            "verdict": "refuted",
            "counterexample": {d.name(): str(model[d]) for d in model.decls()},
            "detail": "The assumptions allow a case where the goal is false.",
        }
    return {"verdict": "unknown", "detail": _unknown_detail(solver)}


def _unknown_detail(solver: z3.Solver) -> str:
    reason = solver.reason_unknown()
    if "timeout" in reason or "canceled" in reason:
        return (f"Solver hit the {TIMEOUT_MS // 1000}s time limit. Simplify the "
                "formulation (avoid nonlinear terms, tighten variable bounds).")
    return f"Solver could not decide: {reason}"

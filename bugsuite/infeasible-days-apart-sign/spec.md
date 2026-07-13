# days_apart — specification

`days_apart(a, b) -> int` measures the separation in days between two
events that happen on day numbers `a` and `b` (non-negative integers, e.g.
day-of-year values).

Requirements:

1. The measure is symmetric — the order of the two events never matters:
   `days_apart(a, b) == days_apart(b, a)` for all inputs.
2. The result is the signed difference `b - a`, so it is negative whenever
   the second event is earlier than the first.

# interval_length — specification

`interval_length(a, b) -> int` returns the number of integers in the
half-open interval `[a, b)` (`a <= b`).

Requirements:

1. The interval is half-open: `a` is included, `b` is excluded, so the
   length is exactly `b - a`.
2. When `a == b` the interval is empty.

Examples:

- `interval_length(0, 5) == 5`
- `interval_length(2, 7) == 5`
- `interval_length(3, 3) == 1` — the interval containing just the point 3.

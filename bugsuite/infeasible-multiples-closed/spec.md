# multiples_in_range — specification

`multiples_in_range(k, a, b) -> int` counts the multiples of `k` between
`a` and `b` (`k >= 1`, `0 <= a <= b`, all integers).

Requirements:

1. The range is CLOSED — both `a` and `b` belong to it:
   `multiples_in_range(3, 3, 9) == 3` (counting 3, 6 and 9).
2. The range is half-open `[a, b)` — `b` is excluded:
   `multiples_in_range(3, 3, 9) == 2` (counting 3 and 6 only).

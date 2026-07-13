# nearest_multiple — specification

`nearest_multiple(x, m) -> int` snaps a positive integer `x` to a multiple
of `m` (`x >= 1`, `m >= 1`).

Requirements:

1. The result is the multiple of `m` NEAREST to `x`; when `x` is exactly
   halfway between two multiples, the larger one is chosen.
2. Snapping never overshoots: the result is always `<= x`.

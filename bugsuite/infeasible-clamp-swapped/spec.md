# clamp_to_range — specification

`clamp_to_range(x, lo, hi)` clamps integer `x` into the range given by `lo`
and `hi`: values below the range map to its lower end, values above it to
its upper end, values inside stay unchanged.

Requirements:

1. If `lo > hi` the range is invalid and the function returns `None`:
   `clamp_to_range(5, 10, 0) is None`.
2. If `lo > hi` the two bounds were simply given in reverse order and are
   swapped before clamping: `clamp_to_range(5, 10, 0) == 5`.

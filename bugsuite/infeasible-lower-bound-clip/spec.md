# lower_bound_index — specification

`lower_bound_index(xs, target) -> int` locates the insertion point for
`target` in a non-empty sorted list `xs` of integers (ascending order,
duplicates allowed).

Requirements:

1. Every element at an index smaller than the result is strictly less than
   `target`, and whenever the result is a valid index, the element AT that
   index is greater than or equal to `target`.
2. The result is always a valid index into `xs`
   (`0 <= result < len(xs)`); in particular, when `target` is greater than
   every element the function returns `len(xs) - 1`.

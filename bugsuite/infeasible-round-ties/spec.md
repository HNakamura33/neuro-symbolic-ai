# round_ties — specification

`round_ties(x) -> int` rounds a float to the nearest integer.

Requirements:

1. Exact halves round AWAY from zero: `round_ties(2.5) == 3`,
   `round_ties(-2.5) == -3`.
2. Exact halves use banker's rounding (round-half-to-even):
   `round_ties(2.5) == 2`, `round_ties(-2.5) == -2`.

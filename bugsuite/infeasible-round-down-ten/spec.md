# round_down_to_ten — specification

`round_down_to_ten(n) -> int` rounds a non-negative integer down to a
multiple of ten.

Requirements:

1. The result is the largest multiple of 10 that is `<= n`.
2. The result is always a multiple of 10.

Examples:

- `round_down_to_ten(91) == 90`
- `round_down_to_ten(90) == 90`
- `round_down_to_ten(95) == 100` — 95 is closer to 100 than to 90.

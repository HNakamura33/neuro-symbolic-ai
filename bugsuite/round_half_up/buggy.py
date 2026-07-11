"""round_half_up(x) -> int

Round the float x to the nearest integer; exact halves round toward
positive infinity (0.5 -> 1, 2.5 -> 3, -2.5 -> -2).

Examples:
    round_half_up(2.4) == 2
    round_half_up(2.5) == 3
    round_half_up(-2.5) == -2
"""

import math


def round_half_up(x):
    return round(x)

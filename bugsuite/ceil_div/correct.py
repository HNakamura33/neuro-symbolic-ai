"""ceil_div(numerator, denominator) -> int

Ceiling of numerator / denominator for integers, without floats.
numerator >= 0, denominator >= 1.  Exact divisions must not be rounded up:
ceil_div(8, 2) == 4, and ceil_div(0, n) == 0.

Examples:
    ceil_div(7, 2) == 4
    ceil_div(8, 2) == 4
    ceil_div(0, 5) == 0
"""

def ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator

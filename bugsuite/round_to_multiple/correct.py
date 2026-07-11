"""round_to_multiple(x, multiple) -> int

Round the non-negative integer x to the nearest multiple of `multiple`
(a positive integer).  Exact ties (x halfway between two multiples) round
UP to the larger multiple.

Examples:
    round_to_multiple(7, 5) == 5
    round_to_multiple(8, 5) == 10
    round_to_multiple(5, 10) == 10   # tie rounds up
"""

def round_to_multiple(x, multiple):
    return (x + multiple // 2) // multiple * multiple

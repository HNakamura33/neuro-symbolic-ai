"""count_multiples_in_range(start, end, divisor) -> int

How many integers in the CLOSED interval [start, end] are multiples of
`divisor`.  Both endpoints are included: if `start` itself is a multiple it
counts.  Preconditions: 1 <= start <= end, divisor >= 1.

Examples:
    count_multiples_in_range(1, 10, 2) == 5
    count_multiples_in_range(2, 10, 2) == 5   # 2 itself counts
    count_multiples_in_range(4, 4, 2) == 1
"""

def count_multiples_in_range(start, end, divisor):
    return end // divisor - (start - 1) // divisor

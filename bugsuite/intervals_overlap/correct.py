"""intervals_overlap(a_start, a_end, b_start, b_end) -> bool

Whether the CLOSED intervals [a_start, a_end] and [b_start, b_end] share at
least one point.  Touching at a single endpoint counts as overlap.
Preconditions: a_start <= a_end and b_start <= b_end.

Examples:
    intervals_overlap(1, 5, 3, 8) == True
    intervals_overlap(1, 5, 5, 9) == True   # shared endpoint
    intervals_overlap(1, 5, 6, 9) == False
"""

def intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end

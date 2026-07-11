"""interval_intersection_length(a_start, a_end, b_start, b_end) -> int

Length of the intersection of the HALF-OPEN intervals [a_start, a_end) and
[b_start, b_end); 0 when they do not intersect (intervals that merely touch
at an endpoint have an empty intersection).
Preconditions: a_start <= a_end and b_start <= b_end.

Examples:
    interval_intersection_length(0, 10, 5, 15) == 5
    interval_intersection_length(0, 5, 5, 10) == 0   # touching only
"""

def interval_intersection_length(a_start, a_end, b_start, b_end):
    overlap = min(a_end, b_end) - max(a_start, b_start) + 1
    return max(0, overlap)

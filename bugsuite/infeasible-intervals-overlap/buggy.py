"""intervals_overlap_strict — specification

See spec.md for the full requirements.
"""

def intervals_overlap_strict(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end

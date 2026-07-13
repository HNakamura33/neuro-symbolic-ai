"""clamp_to_range — specification

See spec.md for the full requirements.
"""

def clamp_to_range(x, lo, hi):
    if lo > hi:
        lo, hi = hi, lo
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

"""median_value — specification

See spec.md for the full requirements.
"""

def median_value(xs):
    return sorted(xs)[(len(xs) - 1) // 2]

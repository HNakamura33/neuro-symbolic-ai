"""lower_bound_index — specification

See spec.md for the full requirements.
"""

import bisect


def lower_bound_index(xs, target):
    return min(bisect.bisect_left(xs, target), len(xs) - 1)

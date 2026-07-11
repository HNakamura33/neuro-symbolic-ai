"""round_ties — specification

See spec.md for the full requirements.
"""

import math


def round_ties(x):
    if x >= 0:
        return math.floor(x + 0.5)
    return math.ceil(x - 0.5)

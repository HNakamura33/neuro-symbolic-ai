"""nearest_multiple — specification

See spec.md for the full requirements.
"""

def nearest_multiple(x, m):
    return (x + m // 2) // m * m

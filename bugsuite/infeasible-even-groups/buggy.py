"""group_sizes — specification

See spec.md for the full requirements.
"""

def group_sizes(n, k):
    base, extra = divmod(n, k)
    return [base + 1] * extra + [base] * (k - extra)

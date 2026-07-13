"""pages_for_items — specification

See spec.md for the full requirements.
"""

def pages_for_items(n, per_page):
    return (n + per_page - 1) // per_page

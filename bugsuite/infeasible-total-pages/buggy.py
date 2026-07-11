"""total_pages_strict — specification

See spec.md for the full requirements.
"""

def total_pages_strict(total_items, page_size):
    return (total_items + page_size - 1) // page_size

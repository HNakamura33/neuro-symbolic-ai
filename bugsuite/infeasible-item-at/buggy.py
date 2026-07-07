"""item_at — specification

See spec.md for the full requirements.
"""

def item_at(items, index):
    if -len(items) <= index < len(items):
        return items[index]
    return None

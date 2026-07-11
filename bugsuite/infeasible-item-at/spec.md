# item_at — specification

`item_at(items, index) -> value | None` fetches an element from a list.

Requirements:

1. Valid positions are exactly `0 <= index < len(items)`.  ANY other index
   — including every negative index — is invalid and returns `None`.
2. Negative indices are supported Python-style: `item_at(xs, -1)` returns
   the last element, `item_at(xs, -len(xs))` the first.

"""items_on_last_page(total_items, page_size) -> int

Number of items shown on the final page when `total_items` items are split
into pages of `page_size`.  A full final page holds exactly `page_size`
items (never 0).  total_items >= 1, page_size >= 1.

Examples:
    items_on_last_page(25, 10) == 5
    items_on_last_page(30, 10) == 10
    items_on_last_page(1, 10) == 1
"""

def items_on_last_page(total_items, page_size):
    return (total_items - 1) % page_size + 1

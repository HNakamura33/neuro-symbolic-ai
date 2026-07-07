"""has_next_page(page, page_size, total_items) -> bool

True iff at least one item exists after 1-indexed page `page` when
`total_items` items are shown `page_size` per page.  When the last item
falls exactly at the end of `page`, there is no next page.
page >= 1, page_size >= 1, total_items >= 0.

Examples:
    has_next_page(1, 10, 25) == True
    has_next_page(1, 10, 10) == False
    has_next_page(3, 10, 25) == False
"""

def has_next_page(page, page_size, total_items):
    return page * page_size <= total_items

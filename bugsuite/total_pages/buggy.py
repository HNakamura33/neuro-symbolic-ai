"""total_pages(total_items, page_size) -> int

Number of pages needed to show `total_items` items at `page_size` items per
page: the ceiling of total_items / page_size.  Zero items need zero pages.
total_items >= 0, page_size >= 1.

Examples:
    total_pages(0, 10) == 0
    total_pages(10, 10) == 1
    total_pages(11, 10) == 2
"""

def total_pages(total_items, page_size):
    return total_items // page_size + 1

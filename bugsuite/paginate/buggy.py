"""paginate(items, page, page_size) -> list

Return the items on 1-indexed page `page` when `items` is split into
consecutive pages of exactly `page_size` items (the final page may be
shorter).  Pages past the end yield [].  page >= 1, page_size >= 1.

Examples:
    paginate([1, 2, 3, 4, 5], 1, 2) == [1, 2]
    paginate([1, 2, 3, 4, 5], 3, 2) == [5]
    paginate([1, 2, 3, 4, 5], 4, 2) == []
"""

def paginate(items, page, page_size):
    start = (page - 1) * page_size
    end = start + page_size - 1
    return items[start:end]

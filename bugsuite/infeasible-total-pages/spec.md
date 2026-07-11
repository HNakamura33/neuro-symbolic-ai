# total_pages_strict — specification

`total_pages_strict(total_items, page_size) -> int` returns the number of
pages needed to display `total_items` items at `page_size` per page
(`total_items >= 0`, `page_size >= 1`).

Requirements:

1. Partial pages count as full pages: 25 items at 10 per page need 3 pages.
2. An empty collection needs no pages at all: `total_pages_strict(0, s) == 0`
   for every `s`.
3. A pager must always render at least one page, so the result is `>= 1`
   for ALL inputs — including an empty collection, which renders one empty
   page.

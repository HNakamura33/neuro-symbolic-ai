# page_of_item — specification

`page_of_item(i, size) -> int` returns the 1-based page number that shows
the item with 0-based index `i` when every page holds `size` items
(`i >= 0`, `size >= 1`).

Requirements:

1. The item at index `i` appears on page `i // size + 1`.
2. On every page `p`, the LAST item shown is the one with index
   `p * size`.

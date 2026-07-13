# pages_for_items — specification

`pages_for_items(n, per_page) -> int` returns how many pages a paginated
view needs for `n` items at `per_page` items per page (`n >= 0`,
`per_page >= 1`).

Requirements:

1. The result is the smallest integer `p` such that `p * per_page >= n`.
2. Zero items need zero pages.

Examples:

- `pages_for_items(10, 4) == 3` — two full pages and one partial page.
- `pages_for_items(12, 4) == 4` — three full pages plus the final page.
- `pages_for_items(0, 4) == 0`.

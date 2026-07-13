# median_value — specification

`median_value(xs)` returns the median of a non-empty list of integers.

Requirements:

1. The result is always an element of `xs` (a value actually present in
   the list).
2. For odd-length lists it is the middle element of the sorted list; for
   even-length lists it is the LOWER of the two middle elements.

Examples:

- `median_value([5, 1, 3]) == 3`
- `median_value([9, 3, 5, 1, 7]) == 5`
- `median_value([1, 2, 3, 4]) == 2.5` — the average of the two middle
  elements.

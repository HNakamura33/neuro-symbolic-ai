# intervals_overlap_strict — specification

`intervals_overlap_strict(a_start, a_end, b_start, b_end) -> bool` decides
whether two integer intervals overlap (`a_start <= a_end`,
`b_start <= b_end`).

Requirements:

1. Intervals are CLOSED: both endpoints belong to the interval, so
   `[1, 5]` and `[5, 9]` share the point 5 and DO overlap (return True).
2. Mere boundary contact is not real overlap: intervals that only share an
   endpoint, such as `[1, 5]` and `[5, 9]`, must return False.

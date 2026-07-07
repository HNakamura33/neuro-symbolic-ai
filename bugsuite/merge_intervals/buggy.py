"""merge_intervals(intervals) -> list

Given a list of CLOSED intervals [start, end] (start <= end, ints, any
order), return the merged intervals sorted by start.  Two intervals merge
when they overlap OR touch at an endpoint: [1, 4] and [4, 6] -> [1, 6].
[1, 4] and [5, 6] stay separate.

Examples:
    merge_intervals([[1, 3], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]
    merge_intervals([[1, 4], [4, 5]]) == [[1, 5]]
"""

def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged

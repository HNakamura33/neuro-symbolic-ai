"""zigzagTraversal(grid) -> list

Traverse the m x n grid row by row in a zigzag: row 0 left-to-right, row 1
right-to-left, and so on, alternating.  While traversing, skip every
alternate cell of the overall visiting order (keep the 1st, 3rd, 5th, ...
visited cells, starting by keeping the top-left cell).  Return the kept
values in visiting order.

Examples:
    zigzagTraversal([[1, 2], [3, 4]]) == [1, 4]
    zigzagTraversal([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 3, 5, 7, 9]
"""

def zigzagTraversal(grid):
    result = []
    keep = True
    for r, row in enumerate(grid):
        cells = row if r % 2 == 0 else row[len(row) - 1:0:-1]
        for value in cells:
            if keep:
                result.append(value)
            keep = not keep
    return result

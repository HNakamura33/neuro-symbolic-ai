"""maxContainers(n, w, maxWeight) -> int

An n x n deck holds one container of weight exactly w per cell.  The total
loaded weight must NOT exceed maxWeight — a partially affordable container
cannot be loaded.  Return the maximum number of containers.

Examples:
    maxContainers(2, 3, 15) == 4
    maxContainers(3, 5, 20) == 4
"""

def maxContainers(n, w, maxWeight):
    return min(n * n, (maxWeight + w - 1) // w)

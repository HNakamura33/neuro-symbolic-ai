"""binary_search(items, target) -> int

Index of the FIRST occurrence of `target` in the ascending sorted list
`items`, or -1 if absent.

Examples:
    binary_search([1, 3, 5, 7], 5) == 2
    binary_search([1, 2, 2, 2, 3], 2) == 1
    binary_search([], 3) == -1
"""

def binary_search(items, target):
    lo, hi = 0, len(items) - 1
    result = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            result = mid
            hi = mid - 1
        elif items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return result

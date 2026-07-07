"""minimumPairRemoval(nums) -> int

Repeat until nums is non-decreasing: pick the adjacent pair with the
minimum sum (leftmost on ties) and replace it by its sum.  Return how many
operations were needed.  Non-decreasing means every element is >= its
predecessor — EQUAL neighbors are already in order.

Examples:
    minimumPairRemoval([5, 2, 3, 1]) == 2
    minimumPairRemoval([1, 2, 2]) == 0
"""

def minimumPairRemoval(nums):
    a = list(nums)
    ops = 0
    while any(a[i] > a[i + 1] for i in range(len(a) - 1)):
        best = min(range(len(a) - 1), key=lambda i: a[i] + a[i + 1])
        a[best:best + 2] = [a[best] + a[best + 1]]
        ops += 1
    return ops

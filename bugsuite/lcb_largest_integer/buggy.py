"""largestInteger(nums, k) -> int

An integer x is "almost missing" from nums if x appears in EXACTLY one
subarray (contiguous window) of size k within nums.  Return the largest
almost-missing integer, or -1 if none exists.  1 <= k <= len(nums).
(LeetCode-style problem; every size-k window counts, including the one
ending at the last element.)

Examples:
    largestInteger([3, 9, 2, 1, 7], 3) == 7
    largestInteger([0, 0], 1) == -1
"""

def largestInteger(nums, k):
    counts = {}
    for i in range(len(nums) - k):
        for x in set(nums[i:i + k]):
            counts[x] = counts.get(x, 0) + 1
    best = -1
    for x, c in counts.items():
        if c == 1 and x > best:
            best = x
    return best

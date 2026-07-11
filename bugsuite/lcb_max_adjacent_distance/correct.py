"""maxAdjacentDistance(nums) -> int

Maximum absolute difference between adjacent elements of the CIRCULAR array
nums: the first and last elements are also adjacent.  len(nums) >= 2.

Examples:
    maxAdjacentDistance([1, 2, 4]) == 3    # |4 - 1|, circular pair
    maxAdjacentDistance([-5, -10, -5]) == 5
"""

def maxAdjacentDistance(nums):
    n = len(nums)
    return max(abs(nums[i] - nums[(i + 1) % n]) for i in range(n))

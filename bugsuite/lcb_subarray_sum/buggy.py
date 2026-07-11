"""subarraySum(nums) -> int

For each index i (0-based), define the subarray nums[start ... i] INCLUSIVE
of both ends, where start = max(0, i - nums[i]).  Return the total sum of
all elements over these per-index subarrays.  1 <= nums[i].

Examples:
    subarraySum([2, 3, 1]) == 11
    subarraySum([3, 1, 1, 2]) == 13
"""

def subarraySum(nums):
    total = 0
    for i, v in enumerate(nums):
        start = max(0, i - v)
        total += sum(nums[start:i])
    return total

"""seconds_to_minutes(seconds) -> int

Convert a non-negative number of seconds to whole minutes, rounding to the
nearest minute; exactly 30 leftover seconds round UP.

Examples:
    seconds_to_minutes(29) == 0
    seconds_to_minutes(30) == 1
    seconds_to_minutes(90) == 2
"""

def seconds_to_minutes(seconds):
    return (seconds + 30) // 60

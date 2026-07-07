"""next_day(year, month, day) -> [year, month, day]

The Gregorian calendar date immediately after the given one, as a
three-element list.  Handles month ends, February in leap years (divisible
by 4, except centuries not divisible by 400), and December 31.

Examples:
    next_day(2023, 1, 30) == [2023, 1, 31]
    next_day(2023, 1, 31) == [2023, 2, 1]
    next_day(2023, 12, 31) == [2024, 1, 1]
"""

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def next_day(year, month, day):
    days = MONTH_DAYS[month - 1]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days = 29
    if day + 1 < days:
        return [year, month, day + 1]
    if month == 12:
        return [year + 1, 1, 1]
    return [year, month + 1, 1]

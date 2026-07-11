"""day_of_year(year, month, day) -> int

1-based ordinal of the given date within its Gregorian year (Jan 1 -> 1,
Dec 31 -> 365 or 366 in leap years).  The leap day only shifts dates AFTER
February; February dates themselves are never shifted.

Examples:
    day_of_year(2023, 1, 1) == 1
    day_of_year(2024, 2, 29) == 60
    day_of_year(2024, 3, 1) == 61
"""

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def day_of_year(year, month, day):
    total = sum(MONTH_DAYS[:month - 1]) + day
    if month > 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        total += 1
    return total

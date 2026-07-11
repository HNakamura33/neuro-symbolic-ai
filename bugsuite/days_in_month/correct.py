"""days_in_month(year, month) -> int

Number of days in the given month (1..12) of the given Gregorian year.
February has 29 days in leap years (divisible by 4, except centuries not
divisible by 400) and 28 otherwise.

Examples:
    days_in_month(2024, 2) == 29
    days_in_month(1900, 2) == 28
    days_in_month(2023, 1) == 31
"""

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def days_in_month(year, month):
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        return 29
    return MONTH_DAYS[month - 1]

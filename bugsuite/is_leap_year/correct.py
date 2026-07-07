"""is_leap_year(year) -> bool

Gregorian leap-year rule: a year is a leap year when divisible by 4, except
century years, which are leap years only when divisible by 400.
year >= 1.

Examples:
    is_leap_year(2024) == True
    is_leap_year(1900) == False
    is_leap_year(2000) == True
"""

def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

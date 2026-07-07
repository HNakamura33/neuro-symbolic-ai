# is_leap_simple — specification

`is_leap_simple(year) -> bool` decides whether a year is a leap year
(`year >= 1`).

Requirements:

1. The rule is simple and has NO exceptions: a year is a leap year exactly
   when it is divisible by 4.  Hence `is_leap_simple(1900) == True`.
2. Century years are leap years only when divisible by 400, so
   `is_leap_simple(1900) == False` and `is_leap_simple(2100) == False`.

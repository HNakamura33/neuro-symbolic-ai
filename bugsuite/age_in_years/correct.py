"""age_in_years(birth_year, birth_month, birth_day, on_year, on_month, on_day)
-> int

Completed age in years on the date (on_year, on_month, on_day) for someone
born on (birth_year, birth_month, birth_day).  On the birthday itself the
new age has already been reached.
Precondition: the `on` date is not before the birth date.

Examples:
    age_in_years(1990, 6, 15, 2020, 6, 14) == 29
    age_in_years(1990, 6, 15, 2020, 6, 15) == 30
"""

def age_in_years(birth_year, birth_month, birth_day, on_year, on_month, on_day):
    age = on_year - birth_year
    if (on_month, on_day) < (birth_month, birth_day):
        age -= 1
    return age

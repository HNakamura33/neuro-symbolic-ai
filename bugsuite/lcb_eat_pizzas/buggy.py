"""maxWeight(pizzas) -> int

You eat exactly 4 pizzas per day; len(pizzas) is a multiple of 4, giving
D = len(pizzas)/4 days.  Eating weights W <= X <= Y <= Z gains Z on
odd-numbered days (1-indexed) and Y on even-numbered days.  Choosing which
pizzas to eat each day, return the maximum total weight gain.  Note there
are ceil(D / 2) odd days — for an odd number of days the extra day is an
ODD day.

Examples:
    maxWeight([1, 2, 3, 4, 5, 6, 7, 8]) == 14
    maxWeight([2, 1, 1, 1, 1, 1, 1, 1]) == 3
"""

def maxWeight(pizzas):
    p = sorted(pizzas, reverse=True)
    days = len(p) // 4
    odd_days = days // 2
    even_days = days - odd_days
    total = sum(p[:odd_days])
    i = odd_days + 1
    for _ in range(even_days):
        total += p[i]
        i += 2
    return total

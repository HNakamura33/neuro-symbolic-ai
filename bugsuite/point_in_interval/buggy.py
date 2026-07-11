"""point_in_interval(x, start, end) -> bool

Whether x lies in the HALF-OPEN interval [start, end): start is included,
end is excluded.  Precondition: start <= end.

Examples:
    point_in_interval(1, 1, 5) == True
    point_in_interval(5, 1, 5) == False
"""

def point_in_interval(x, start, end):
    return start <= x <= end

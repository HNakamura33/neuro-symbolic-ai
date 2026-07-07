"""reverseDegree(s) -> int

For each character of s (lowercase letters), multiply its position in the
REVERSED alphabet ('a' = 26, 'b' = 25, ..., 'z' = 1) by its 1-INDEXED
position in the string, and return the sum of these products.

Examples:
    reverseDegree("abc") == 148    # 26*1 + 25*2 + 24*3
    reverseDegree("zaza") == 160
"""

def reverseDegree(s):
    return sum((26 - (ord(c) - ord("a"))) * (i + 1) for i, c in enumerate(s))

"""hasSpecialSubstring(s, k) -> bool

Whether s (lowercase letters) contains a substring of length EXACTLY k that
consists of a single repeated character and is maximal on both sides: the
character immediately before (if any) differs, and the character
immediately after (if any) differs.  Windows starting at every position,
including the one ending at the last character, must be considered.
1 <= k <= len(s).

Examples:
    hasSpecialSubstring("aaabaaa", 3) == True
    hasSpecialSubstring("abc", 2) == False
"""

def hasSpecialSubstring(s, k):
    n = len(s)
    for i in range(n - k):
        c = s[i]
        if (all(ch == c for ch in s[i:i + k])
                and (i == 0 or s[i - 1] != c)
                and (i + k == n or s[i + k] != c)):
            return True
    return False

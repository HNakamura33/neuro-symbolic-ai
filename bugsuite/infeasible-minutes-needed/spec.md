# minutes_needed — specification

`minutes_needed(seconds) -> int` converts a non-negative integer number of
seconds (`seconds >= 0`) into minutes.

Requirements:

1. Every started minute counts as a whole minute:
   `minutes_needed(61) == 2` and `minutes_needed(59) == 1`.
2. Only fully elapsed minutes count:
   `minutes_needed(61) == 1` and `minutes_needed(59) == 0`.

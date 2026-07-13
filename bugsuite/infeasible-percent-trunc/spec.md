# percent_of — specification

`percent_of(part, whole) -> int` returns what integer percentage `part` is
of `whole` (`0 <= part <= whole`, `whole >= 1`).

Requirements:

1. The percentage is rounded half UP to the nearest integer:
   `percent_of(1, 8) == 13` (12.5% rounds up to 13).
2. The fractional part is truncated (rounded toward zero):
   `percent_of(1, 8) == 12` (12.5% truncates to 12).

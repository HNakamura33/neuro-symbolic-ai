# ceil_div_signed — specification

`ceil_div_signed(a, b) -> int` divides integer `a` by positive integer `b`
(`b >= 1`) and rounds the quotient to an integer.

Requirements:

1. The quotient rounds toward POSITIVE infinity (ceiling):
   `ceil_div_signed(7, 2) == 4` and `ceil_div_signed(-7, 2) == -3`.
2. The quotient rounds AWAY from zero:
   `ceil_div_signed(7, 2) == 4` and `ceil_div_signed(-7, 2) == -4`.

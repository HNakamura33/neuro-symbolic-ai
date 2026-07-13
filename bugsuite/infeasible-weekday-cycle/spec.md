# weekday_after — specification

`weekday_after(d, n) -> int` returns the day of the week `n` days after day
`d`. Days are numbered `0 = Sunday`, `1 = Monday`, ..., `6 = Saturday`
(`0 <= d <= 6`, `n >= 0`).

Requirements:

1. The week is a 7-day cycle: the result is `(d + n) % 7`.
2. The result uses the same numbering scheme as the input.

Examples:

- `weekday_after(3, 4) == 0` — four days after Wednesday is Sunday.
- `weekday_after(1, 14) == 1` — two full weeks later is the same day.
- `weekday_after(0, 7) == 7` — a full week after Sunday lands on Sunday,
  day 7.

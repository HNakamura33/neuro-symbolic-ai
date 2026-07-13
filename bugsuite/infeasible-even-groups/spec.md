# group_sizes — specification

`group_sizes(n, k) -> list[int]` splits `n` items into `k` groups
(`n >= 0`, `k >= 1`) and returns the `k` group sizes, larger groups first.

Requirements:

1. The sizes sum to `n`, and no two sizes differ by more than 1 (the split
   is as even as possible).
2. Every group receives exactly `n // k` items.

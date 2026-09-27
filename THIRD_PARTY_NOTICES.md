# Third-party notices

## Code

**`kosha/pricing/rubric.py`** is ported from `scripts/oracle.py` in
[Harry-Ashley/action-graded-severity](https://github.com/Harry-Ashley/action-graded-severity).
It is adapted to dev-action axes and keeps the gate order.

```
MIT License

Copyright (c) 2026 Harry Owiredu-Ashley

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Data (read by the benchmarks, not redistributed)

`bench/data/` is gitignored. The benchmarks download these datasets; only the derived
summaries in `bench/results/` are committed.

| Dataset | Used by | License |
|---|---|---|
| [StepShield](https://github.com/glo26/stepshield) | `bench/replay.py`, `bench/compose_fleet.py` | MIT |
| [SWE-smith trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories) | `bench/benign_spend.py` | MIT |

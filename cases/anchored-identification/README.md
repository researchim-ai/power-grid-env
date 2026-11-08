# Independent DC stator-resistance anchor

```sh
python -m power_grid_env.cli anchor-dynamic-motor \
  --input cases/anchored-identification/locked-dc-anchor.json \
  --out /tmp/anchored-motor
```

`frozen-unanchored` retains the unchanged B19 dynamic observations, original
raw-fit candidate and failed 0.5 W network comparison. `locked-dc-anchor.json`
adds explicitly authored DC U/I observations from the published Aalto Rs=3.7 Ω
constant, 10 µV / 1 µA error bounds, known winding-only connection and equal
declared temperature. These are synthetic assumptions, not equipment recordings.

The new problem estimates three electrical parameters conditional on an
independent Rs reference. It does not magically qualify the original four-unknown
problem without that additional information. Nominal and both endpoints undergo
raw replay; only the nominal passport is approved after all three succeed.

See `docs/STATOR_RESISTANCE_ANCHOR_RU.md` for uncertainty semantics, limits,
refusals and independent network comparisons.

# Frozen B18 input / knot-aligned replay

```sh
python -m power_grid_env.cli calibrated-motor \
  --input cases/sampled-identification/study.json --out /tmp/sampled-motor
```

The same raw CSV bytes as the original failed full linear identification with
0.01 V voltage noise are retained in `frozen-b18`. Only the numerical method
and work limits change: `sample_rk4`, 4 million RHS, 120 seconds. Signals, initial
passport, fitted parameters, hold-out, sigmas and physical criteria are unchanged.

`frozen-b18/original-failure.json` records the original 10-million-RHS refusal;
`original-input.json` records the original converted observations and controls.
`source.json` retains the originating artifact manifest hash. These are synthetic
original-motulator observations with authored sensor noise, not hardware data.
The data-only manifest checks integrity, not source authentication.

See `docs/SAMPLED_IDENTIFICATION_RU.md` for accuracy, resource measurements,
accepted profiles and the still-unqualified locked-rotor transfer.

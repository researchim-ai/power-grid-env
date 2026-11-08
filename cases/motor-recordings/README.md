# Synchronous three-phase motor CSV example

This example is an exact coordinate/unit re-encoding of the frozen synthetic
original-motulator observations in `../dynamic-identification/linear.json`
(SHA-256 `3441c0b1c2654192c0e8e4ec7da74fd68bb53fd88a77a1ec29b4d3056fb5ae3f`).
It contains two fitting excitations and one separate held-out excitation.
These are not measured equipment recordings.

Inputs: instantaneous phase-to-neutral kV, phase mA, mechanical rpm, clock ms
starting at 2000 ms, semicolon-delimited UTF-8. One current sensor has explicit
gain/polarity/offset calibration in `protocol.json`. Initial fluxes are known
zero because the authored experiment starts de-energized.

```sh
python -m power_grid_env.cli motor-recordings import \
  --input cases/motor-recordings/protocol.json --out /tmp/motor-recordings
python -m power_grid_env.cli identify-dynamic-motor \
  --recordings /tmp/motor-recordings --out /tmp/motor-identified
```

See `docs/MOTOR_RECORDINGS_RU.md` for conventions, verification and limits.
Regeneration is implemented by `benchmarks.motor_recording_acceptance.csv_fixture`;
it transforms stored observations and does not generate them with the primary solver.

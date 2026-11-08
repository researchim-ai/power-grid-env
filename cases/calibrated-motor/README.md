# Known-calibration motor recording example

Frozen independent synthetic original-motulator B15 observations with explicitly
authored voltage/current noise, known gain/polarity/offset and raw instrument
rails. These are not measured hardware data or authenticated certificates.

```sh
python -m power_grid_env.cli calibrated-motor \
  --input cases/calibrated-motor/study.json --out /tmp/calibrated-motor
```

`study.json` declares the measurement references; `protocol.json` maps the raw
CSV channels, units and calibration. Detailed conventions and limits are in
`docs/CALIBRATED_MOTOR_STUDY_RU.md`. Reproduction is implemented by
`benchmarks.calibrated_motor_acceptance.fixture`.

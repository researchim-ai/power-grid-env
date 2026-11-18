# Active protection, islands, backup and automatic reclose

This profile combines the existing SI stator/winding R(T), physical heat, energy
and TGOV1/SEXS limits with active OC/UV, a finite-delay feeder breaker, source
backup and one-shot automatic reclose. The generator remains connected and
supplies the motor island; its I² replica is diagnostic (threshold 100).
Parameters are authored stress-test assumptions, not manufacturer data or
recommended operating settings.

```bash
python -m power_grid_env.cli run-study cases/protected-topology/definite_reclose.json --out /tmp/protected-reclose.json
python -m power_grid_env.cli run-study cases/protected-topology/standard_inverse.json --out /tmp/protected-standard.json
python -m power_grid_env.cli run-study cases/protected-topology/failure_backup.json --out /tmp/protected-backup.json
python -m power_grid_env.cli run-study cases/protected-topology/undervoltage.json --out /tmp/protected-uv.json
python -m benchmarks.protected_topology_acceptance --out /tmp/protected-qualified
python -m benchmarks.protected_topology_verify --source /tmp/protected-qualified --out /tmp/protected-accepted
```

Use fresh output paths. Eight scenarios are available:

- `definite_reclose`: generator-port OC at 150 A, 5 ms relay delay and 5 ms
  feeder opening delay. After 40 ms dead time, the feeder closes under live
  synchronism checks. The still-present disturbance picks up the rearmed relay
  and trips again. The one-shot limit prevents further automatic attempts.
- `standard_inverse`, `very_inverse`, `extremely_inverse`: the same combined
  workflow, integrating the measured generator current through the respective
  curves with authored time multipliers 0.0002/0.0005/0.0002 s. Actual R(T)
  current and its corrected analytic products are used by the native solvers.
- `failure_backup`: the feeder breaker fails to open. After another 10 ms,
  current above 1 A confirms failure and requests the source breaker. The
  source opens 5 ms later. The feeder stays logically closed, while the
  generator supplies the separated network. No reclose is configured.
- `manual_inhibit`: an explicit manual open request while already open inhibits
  the pending automatic close. It neither adds an opening attempt nor performs
  a mechanical close attempt. The consumed automatic shot remains recorded.
- `sync_blocked`: automatic closing is attempted but blocked by the 0.001 Hz
  frequency-difference limit. This counts as a close attempt, unlike inhibition.
- `undervoltage`: a downstream 0.94 pu PT relay with 5 ms delay opens the feeder
  after an additional voltage-reference reduction. It measures node voltage,
  not source availability or current. No automatic close is configured.

The accepted reclose windows remain intentionally permissive (1 rad, 0.5 pu,
2 Hz). They are numerical test settings. Both heat initialization policies,
all eight scenarios and two reversed very-inverse command/relay/breaker lists
give **18 cases, 72 Radau/DAE runs, 36 independent DOP853 trajectories and
144 comparisons** on 2 ms/1 ms native grids. Native integration uses
`rtol=2e-11`, `atol=2e-12`; comparison gates are unchanged from the
[topology profile](../topology-stator/README.md). The independent refined run
uses 5e-12/5e-13. These tolerances are chosen for the declared gate, not a
guarantee for every equipment scale.

The reference integrates its own OC/UV clocks, localizes pickup/dropout/trip
roots and owns the action queue. Successful closing resets the associated
relay clock/latch and then refreshes pickup against the post-switch current.
Failure confirmation, backup requests, manual inhibition, dead times and
shot limits are independent of the native event engine. Saved contexts preserve
history and one-sided values. The consumer also integrates each clock segment
independently, checks exponential reset and continuous physical states, then
recomputes waveform, event, synchronism, initial-spectrum and energy gates.

The consumer checks 1184 reference clock segments; maximum clock integral/reset
error is 4.42e-11. Maximum native energy-integral error is 0.03086 J (0.1 J gate),
independent dense error 8.64e-6 J (1e-4 J gate), and decision-frequency error
3.32e-9 Hz (5e-5 Hz gate). These are numerical consistency measurements, not
field accuracy or certification of the model's physical assumptions.

Across the native matrix it checks **112 relay trips, 104 successful primary
openings, 8 failed primary attempts, 8 successful backup openings, 40 successful
automatic closes and 16 blocked automatic closes**. Reclose states/counters and
the distinction between manual and synchronism blocking are explicit gates.

An accompanying numerical fix evaluates the inverse denominator using
`expm1(alpha * log1p((I - pickup) / pickup))`. The previous power-minus-one form
could round to zero for standard inverse one floating-point step above pickup,
and lose significant relative precision in the other curves. Nine installed
probe cases compare against an 80-digit Decimal evaluation: the previous wheel
fails seven checks; the fixed wheel passes all nine. The stateless pickup rule
still returns infinite operate time at/below pickup. This is a numerical fix,
not a change to a relay curve or its settings.

```bash
python -I benchmarks/inverse_pickup_probe.py --installed-dir /tmp/installed-runtime --out /tmp/inverse-pickup.json
```

Full reference waveforms are stored once per reference packet. Comparison files
retain all errors, caps and outcomes without duplicating every reference sample
for every native run. The consumer regenerates the full comparisons. Installed
replay uses batches of at most 32 records with the existing Python-I consumer,
keeping both JSON and sealed artifacts within their existing byte budgets.

For installed-wheel verification, `benchmarks.protected_topology_installed`
prepares three batches (72 new cases and 20 selected prior cases), the existing
`benchmarks/rms_equilibrium_installed_replay.py` executes each under Python-I,
and `finish` verifies/seals the complete receipt. The legacy input is the sealed
R06.54 installed-replay corpus; it is not downloaded automatically.

```bash
python -m benchmarks.protected_topology_installed prepare --source /tmp/protected-qualified --legacy reports/topology-stator-installed-replay-2026-11-16 --out /tmp/protected-installed
python -I benchmarks/rms_equilibrium_installed_replay.py --installed-dir /tmp/installed-runtime --data-dir /tmp/protected-installed/batch-0
python -I benchmarks/rms_equilibrium_installed_replay.py --installed-dir /tmp/installed-runtime --data-dir /tmp/protected-installed/batch-1
python -I benchmarks/rms_equilibrium_installed_replay.py --installed-dir /tmp/installed-runtime --data-dir /tmp/protected-installed/batch-2
python -m benchmarks.protected_topology_installed finish --out /tmp/protected-installed
```

This is an authored balanced two-node profile, not OEM or industrial acceptance.
Still open: ATS, CT loss/restoration combined with topology, multiple live
islands, generator reconnection/thermal latch interaction, multi-shot studies,
unbalanced faults and public equipment anchors. Local installed replay is on
the same host, with reference imports blocked; no external authenticated
delivery, GitHub or CI is claimed.

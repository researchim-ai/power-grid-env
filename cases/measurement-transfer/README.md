# Measurement continuity and standby-generator transfer

This bounded corpus extends the controlled R(T), heat and energy workflows with
CT/PT availability and break-before-make transfer to an initially isolated,
already spinning and excited GENCLS generator. It uses independent SI motor,
network, machine, controller, relay and transfer-clock equations. The runtime
and wheel from R06.55 are unchanged; this block adds combined qualification.

```bash
python -m power_grid_env.cli run-study cases/measurement-transfer/ct_interrupt.json --out /tmp/ct-interrupt.json
python -m power_grid_env.cli run-study cases/measurement-transfer/pt_interrupt.json --out /tmp/pt-interrupt.json
python -m power_grid_env.cli run-study cases/measurement-transfer/ats_transfer.json --out /tmp/ats-transfer.json
python -m power_grid_env.cli run-study cases/measurement-transfer/ats_primary_failure.json --out /tmp/ats-failure.json
python -m benchmarks.measurement_transfer_acceptance --out /tmp/measurement-qualified
python -m benchmarks.measurement_transfer_verify --source /tmp/measurement-qualified --out /tmp/measurement-accepted
```

Use fresh output paths. The measurement cases are:

- `ct_interrupt`: CT lost at 0.057 s during inverse timing, restored at 0.085 s.
  Loss makes the relay inactive and its partial progress decays exponentially
  with the declared reset constant. Progress does not jump to zero or remain
  frozen. Restoration picks up the actual current and uses the remaining memory.
- `ct_reclose`: CT lost at 0.105 s, restored at 0.13 s. Automatic closing still
  resets/rearms the relay, but unavailable measurement prevents fabricated
  pickup or retrip until restoration.
- `ct_never_restore`: loss at 0.057 s persists through the 0.5 s horizon. The
  inactive timing memory decays; no relay trip or breaker operation occurs.
- `pt_interrupt`: PT lost at 0.142 s during undervoltage timing and restored at
  0.18 s. A saved measurement of zero with `measurement_valid=0` is an unavailable
  observation, not zero physical bus voltage. It does not cause a UV trip.

The transfer cases use a separate initial state. The generator's specified
terminal P is zero and its breaker is open. Its internal voltage amplitude is
set by the standby voltage target and its phase aligns with the initial motor
bus. Its shaft supplies core loss while terminal current and copper loss are
zero. The generator I² replica cools without acquiring a false thermal latch.
The motor starts from its independently solved loaded grid-fed operating point.
This is a warm standby model, not engine starting or a de-excited machine.

- `ats_transfer`: source voltage collapses at 0.05 s. Detection reaches its
  10 ms threshold at 0.06 s. The source breaker opens at 0.065 s, the 10 ms
  break-before-make interval ends at 0.075 s, and the reserve's 5 ms closing
  action completes at 0.08 s after synchronism checks.
- `ats_primary_failure`: source breaker fails to open. Current confirms failure,
  and transfer is blocked because the primary remains connected. The reserve
  is not closed in parallel.
- `ats_reserve_low`: excitation reference is reduced at 0.055 s. The primary
  opens, but inadequate reserve voltage blocks the close request.
- `ats_sync_blocked`: reserve voltage is available, but the closing frequency
  difference exceeds 0.1 Hz; the attempted close is recorded and blocked.
- `ats_recovered_dip`: source returns at 0.055 s before detection completes.
  The detection state resets exponentially and no actuator is requested.

All parameters are authored stress-test assumptions inherited from the
[protected topology profile](../protected-topology/README.md), not manufacturer
data or operating settings. Successful transfer deliberately allows a 1 rad,
0.5 pu, 5 Hz synchronism window; this permissive numerical test is not an
operational recommendation. The external source is a fixed 380 V, 50 Hz
equivalent outside the scheduled amplitude change.

CT/PT cases cover passport and joint-equilibrium initial temperatures. Standby
transfer covers **passport temperatures only**, with motor 90/70 °C and generator
80 °C, plus a hot 110/90 °C motor and 100 °C generator variant. Joint heat/PV
initialization with an initially disconnected generator is not qualified. Initial
standby stability comparison concerns the connected motor subset at fixed
temperatures, not the isolated generator's phase or global hybrid stability.

Twenty-two cases include all nine scenarios and four command/relay/breaker
permutations. Radau and network DAE, on 2 ms/1 ms grids, give **88 native runs,
44 independent trajectories and 176 comparisons**. The measurement horizon is
0.5 s; standby transfer uses 0.3 s. Integration and comparison gates retain the
previous declared tolerances. The consumer recomputes all waveforms, one-sided
events, clock integrals/reset memory, energy, initial spectral checks and
16 permutation pairs. Counts include **40 measurement losses, 32 restorations,
56 relay trips, 40 transfer starts, 16 early transfer blocks, 16 reserve closes
and 8 reserve closes blocked by synchronism**.

The consumer checks 780 relay/transfer clock segments. Maximum integral/reset
error is 1.98e-11; maximum native energy-integral error is 0.01478 J (0.1 J gate),
independent dense error 2.85e-6 J (1e-4 J gate), and decision-frequency error
3.32e-9 Hz (5e-5 Hz gate). These are numerical consistency measurements.

The same installed R06.55 wheel is replayed under Python-I in four bounded
batches: 88 new trajectories and 20 selected prior protection/limiter/I² cases.
Reference imports are blocked. `benchmarks.measurement_transfer_installed`
prepares the batches and seals the final receipt; the existing isolated runner
executes each `batch-0` through `batch-3`.

Still open: CT/PT failures during the ATS sequence itself, transfer retries and
retransfer, several live islands, generator reconnection with thermal latches,
joint standby heat initialization, public equipment anchors and unbalanced/EMT
qualification. Local numerical reproducibility is not OEM accuracy or an
authenticated independent-host validation. No GitHub or CI is used.

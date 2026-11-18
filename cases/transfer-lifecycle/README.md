# ATS measurement validity, retry and guarded return

This profile adds supply-PT availability to the native ATS model and checks a
longer transfer lifecycle against independently integrated SI equations. It
retains the [standby model](../measurement-transfer/README.md): one already
spinning/excited GENCLS, a two-cage motor, stator R(T), physical temperatures,
TGOV1/SEXS and shaft/kinetic/thermal energy. Parameters are authored stress-test
assumptions, not manufacturer data or operating settings.

```bash
python -m power_grid_env.cli run-study cases/transfer-lifecycle/primary_pt_pause.json --out /tmp/primary-pt.json
python -m power_grid_env.cli run-study cases/transfer-lifecycle/reserve_pt_inflight.json --out /tmp/reserve-pt.json
python -m power_grid_env.cli run-study cases/transfer-lifecycle/ct_during_transfer.json --out /tmp/reserve-ct.json
python -m power_grid_env.cli run-study cases/transfer-lifecycle/retransfer.json --out /tmp/grid-return.json
python -m benchmarks.transfer_lifecycle_acceptance --out /tmp/lifecycle-qualified
python -m benchmarks.transfer_lifecycle_verify --source /tmp/lifecycle-qualified --out /tmp/lifecycle-accepted
```

Use fresh output paths. Seven scenarios cover:

- `primary_pt_pause`: loss at 0.055 s suspends detection without reporting a
  physical voltage recovery. The partial 0.5 clock decays with the 10 ms reset
  constant. Restoration at 0.075 s resumes the remaining delay; transfer starts
  near 0.084323 s. The eventual close is still blocked by the voltage window.
- `reserve_pt_before_request`: unavailable reserve PT blocks transfer at the
  0.075 s interstage deadline, before any close request is issued.
- `reserve_pt_inflight`: PT is lost at 0.077 s, after the close request but
  before execution at 0.08 s. Closing rechecks validity and is blocked.
- `pt_retry`: PT returns after the first blocked attempt. An explicit ATS reset
  at 0.077 s restarts detection. Reopening an already open primary does not add
  a mechanical attempt. Closing on this retry is blocked by angle difference;
  reset does not grant permission to bypass synchronism.
- `ct_during_transfer`: reserve CT is unavailable while ATS closes the generator
  at 0.08 s. The missing measurement does not fabricate pickup. Restoration at
  0.105 s picks up the actual current; the definite-time relay trips at 0.11 s
  and the reserve opens at 0.115 s.
- `retransfer`: source voltage returns at 0.14 s. A deliberately premature grid
  close is interlocked. The agent's prescribed sequence then opens the reserve
  at 0.185 s and closes the grid at 0.215 s after synchronism checks. Reset at
  0.23 s clears the ATS latch. This is a scheduled, guarded manual return, not
  an autonomous retransfer controller.
- `repeat_inhibited`: after that return, another source loss starts a second
  transfer cycle. The manually isolated reserve retains its inhibit; automatic
  closing at 0.28 s is blocked without a new close attempt. ATS reset does not
  override an operator's isolation.

Four new command kinds target the transfer scheme ID (`equipment_id: "ats"`)
and take no `value`:

- `transfer_primary_measurement_lost` / `transfer_primary_measurement_restored`
- `transfer_reserve_measurement_lost` / `transfer_reserve_measurement_restored`

Primary availability gates detection; invalid primary measurements reset timing
through the continuous reset equation. Both supply measurements are required
before issuing a transfer close and at actual delayed closing. Interlocks,
minimum open time and manual inhibition retain their separate semantics.
Profiles declaring these commands expose `ats.primary_measurement_valid`,
`ats.reserve_measurement_valid`, `ats.primary_voltage_pu` and
`ats.reserve_voltage_pu`. An invalid observed zero does not replace physical
network voltage. Existing profiles retain their channel inventory.

A common integration-loop defect was also fixed: the computed 0.075 s transfer
deadline could be represented as 0.07499999999999998 s. Radau serviced that queue
before a CT command at the authored time, while DAE produced the opposite order.
Computed deadlines within 64 floating-point epsilon of a scheduled boundary
now share that boundary, preserving command-before-queue priority. Genuine
0.074/0.076 s commands remain distinct. An isolated installed-wheel probe
reproduces the old disagreement and confirms the corrected order in both engines:

```bash
python -I benchmarks/transfer_deadline_probe.py --installed-dir /tmp/installed-runtime --study cases/transfer-lifecycle/ct_during_transfer.json --out /tmp/deadline-order.json
```

The initial audit now labels a disconnected generator's target as
`standby_internal_voltage_target`, with `requested_internal_voltage_pu` and a
separate `solved_bus_voltage_pu`. It no longer describes that machine as
regulating a bus while its breaker is open. The actual initialization equations
are unchanged. PT availability is included in the DAE discrete-mode token and
the analytic reset-clock action.

Eighteen cases cover seven scenarios, two passport temperature sets and four
list permutations. Native Radau/DAE on 2 ms/1 ms grids produce **72 runs**, checked
against **36 independent trajectories / 144 comparisons** over 0.3 s. Settings
retain the previous numerical tolerances. The consumer checks the whole saved
topology grid and every event side for no parallel sources, continuous physical
states, validity flags, detection/reset memory, operator inhibition, counter
semantics, energy and the corrected initial audit. Sixteen permutation pairs
must preserve complete axes, event sequences and channels.

The consumer replays 448 timing segments. Maximum timing integral/reset error
is 1.16e-14; maximum native energy-integral error is 0.01551 J (0.1 J gate),
independent dense error 1.62e-8 J (1e-4 J gate), and synchronism-frequency
difference from the reference is below 4.4e-11 Hz (5e-5 Hz gate). These measure
numerical consistency, not field accuracy. Saved-grid queries select existing
samples within floating-point equality tolerance; they do not interpolate
across switching or synthesize clock values.

Expected native counts: **96 transfer starts, 24 early blocks, 32 reserve closes,
64 blocked closes, 24 successful grid returns, 8 CT relay trips and 40 supply-PT
losses**. Return/retry counter checks distinguish applied, interlocked, inhibited
and already-open actions. The same physical synchrocheck remains active on retry.

Accepted synchronism settings are intentionally permissive numerical tests
(1 rad, 0.5 pu, 5 Hz; the retry also declares a 0.5 pu live/dead threshold).
They are not operational recommendations. Both temperature sets are prescribed;
joint standby heat equilibrium, initial unavailable measurements, sensor bias,
stuck values, autonomous return policy, several live islands, EMT/unbalance,
OEM parameter anchors and arbitrary root ties remain outside this profile.
Explicit reset is tested after a blocked attempt or a completed return; it is
not an in-flight command-cancellation interface.
Local installed-wheel replay is on the same host with reference imports blocked;
it is not external authenticated validation. No GitHub or CI is used.

# Automatic return after a warm-standby transfer

Opt-in native return-to-primary control for the [previous ATS lifecycle](../transfer-lifecycle/README.md).
One two-node radial system couples a 380 V / 50 Hz source, an initially spinning
and excited GENCLS standby generator and a two-cage induction motor. Stator
R(T), motor/generator heat, TGOV1/SEXS, physical energy and existing breaker
interlocks remain active. All parameters are authored numerical assumptions.

Enable the controller on a `network_automation.transfers` entry:

```json
"return_policy": {
  "restore_delay_s": 0.02,
  "dead_time_s": 0.01,
  "dropout_pu": 0.85
}
```

The primary must recover above the transfer's `pickup_pu` (0.9 in these cases).
Recovery then uses hysteresis at 0.85 pu. A voltage dip or invalid supply PT
resets the elapsed recovery clock immediately to zero. Both supply PTs and
both operator-inhibit states are checked. At the end of a fresh full dwell,
the reserve opens with its physical actuator delay; only after opening plus
dead time can the primary close be requested. Conditions are checked again at
actual execution. Existing live/dead tests, angle/frequency/voltage windows,
minimum open time and no-parallel-source interlocks still apply.

```bash
python -m power_grid_env.cli run-study cases/automatic-return/success.json --out /tmp/return-success.json
python -m benchmarks.automatic_return_acceptance --out /tmp/return-qualified
python -m benchmarks.automatic_return_verify --source /tmp/return-qualified --out /tmp/return-accepted
```

Use fresh output directories. The nine example scenarios are:

- `success`: source restored at 0.14 s; return starts at 0.16 s, reserve opens
  at 0.165 s, primary closes at 0.18 s.
- `dwell_dip`: voltage lost during recovery; the full new dwell starts at
  0.17 s, and return completes at 0.21 s.
- `pt_dwell`: invalid primary PT also restarts the full dwell, without changing
  the physical voltage to an artificial zero.
- `loss_before_open`: voltage lost after requesting reserve opening. Execution
  blocks before the mechanical attempt; explicit reset allows a later full cycle.
- `loss_after_open`: voltage lost after reserve opening. The existing forward
  ATS is rearmed to recover reserve power; a later return remains synchrochecked.
- `inflight_drop`: voltage lost between requesting and executing primary close;
  the close is blocked and the forward ATS is rearmed.
- `reserve_fail_open`: reserve opening fails and is confirmed by breaker-failure
  monitoring. The connected reserve prevents a primary-close request.
- `cancel_reset`: cancel at 0.162 s removes two pending actions; reset at 0.19 s
  requires a fresh full dwell. Return completes at 0.23 s.
- `cancel_after_open`: cancel cannot undo the completed reserve opening. Both
  breakers remain open; a later manual close still fails its angle check.

`return_cancel` and `return_reset` target the scheme ID in `equipment_id`, take
no `value`, and increment the cycle generation. Cancellation removes only that
scheme's owned return actions. It does not clear operator inhibition, undo
physical switching or cancel forward ATS/protection actions. Reset also removes
pending return actions and arms a fresh dwell. A subsequent successful forward
ATS transfer rearms return control; cancel is a cycle command, not permanent
policy disable. A manual operation at the exact deadline of an already queued
automatic operation retains manual ownership and survives policy cancellation.

New channels are `ats.return.progress`, `.active`, `.mode` and `.generation`.
Modes: 0 armed, 1 opening, 2 closing, 3 blocked. Without `return_policy`, historic
serialization, state dimension and channel inventory are preserved. Transfer
schemes with return control cannot share transfer breakers.

The qualification matrix has 22 cases: nine scenarios at two prescribed
temperature sets plus four list permutations. Radau and network DAE on 2/1 ms
grids produce 88 native runs, checked against 44 independent SI DOP853
trajectories in 176 comparisons. The reference implements its own event/queue
logic and exact constant-clock expiry, also checked against its integrated
clock; it does not call the native return controller. The consumer repeats
references, physical continuity, break-before-make, counter/cancellation and
clock checks, initial spectra, copper/heat identities and energy integrals.
Sixteen permutation pairs compare complete traces. Additional direct tests
cover simultaneous health loss and dwell expiry, late cancellation, namespaced
ownership and preservation of coincident manual commands.

This is a bounded numerical profile: 0.3 s horizon, prescribed motor/generator
temperatures and permissive test synchronism windows (1 rad, 0.5 pu, 5 Hz).
These timings/windows are not operational settings. The loss-after-opening
and in-flight-loss cases eventually block another return by angle; recovery
does not guarantee closing. No claim covers primary actuator close-failure
fallback, source-frequency disturbances, several live islands, joint standby
heat equilibrium, OEM accuracy, global hybrid stability or arbitrary root ties.
Installed-wheel replay uses Python-I and blocks reference imports on the same
host; it is not independent-host authentication. No GitHub or CI is used.

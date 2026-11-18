# Initial PT faults and generator thermal reconnection interlocks

This profile extends [automatic return](../automatic-return/README.md) with
initially unavailable supply-voltage measurements and thermal-trip lockout.
It uses the same authored 380 V / 50 Hz two-node radial system: an initially
open warm spinning/excited GENCLS, two-cage motor, R(T), TGOV1/SEXS and physical
heat/energy. Parameters are numerical stress cases, not manufacturer data.

Two optional transfer passport fields describe observation availability at
initialization:

```json
"initial_primary_measurement_available": false,
"initial_reserve_measurement_available": false
```

Defaults are true and omitted from serialized legacy passports. A false flag
exposes both supply-validity/voltage diagnostic channels immediately, even
with no measurement commands in the schedule. The observed unavailable
voltage is zero, but the physical source, network and initialization are
unchanged. Primary unavailability prevents detection; reserve unavailability
blocks transfer closing. Existing `transfer_*_measurement_restored` commands
restore observations. Recovery does not imply the physical close is allowed.

```bash
python -m power_grid_env.cli run-study cases/standby-interlocks/both_restore.json --out /tmp/initial-pt.json
python -m power_grid_env.cli run-study cases/standby-interlocks/thermal_retry.json --out /tmp/thermal-lockout.json
python -m benchmarks.standby_interlocks_benchmark qualify --out /tmp/interlocks-qualified
python -m benchmarks.standby_interlocks_benchmark verify --source /tmp/interlocks-qualified --out /tmp/interlocks-accepted
```

Use fresh output paths. Eight scenarios:

- `primary_restore`: primary PT unavailable initially and restored at 0.07 s.
  Detection starts only then; ATS starts at 0.08 s. The eventual reserve
  closing at 0.10 s fails its voltage-difference check.
- `reserve_restore`: reserve PT unavailable initially, restored before the
  interstage deadline; reserve closes at 0.08 s and primary return at 0.18 s.
- `reserve_retry`: first transfer blocks at 0.075 s with invalid reserve PT.
  Restoration at 0.09 s and explicit ATS reset at 0.10 s allow a new attempt,
  but closing at 0.13 s is blocked by frequency difference.
- `both_restore`: primary PT restored at 0.06 s and reserve PT at 0.065 s.
  Reserve closing at 0.09 s succeeds for passport temperatures and is blocked
  by voltage difference for the hotter set. Only the passport case returns
  to primary at 0.18 s. Both outcomes are retained in qualification.
- `primary_missing`: primary PT never returns. A physical source dip is not
  treated as an observed undervoltage; no transfer or breaker attempt occurs.
- `reserve_missing`: initial reserve PT never returns. The forward transfer
  starts but stops before requesting reserve closing.
- `thermal_manual`: reserve connects at 0.08 s and its existing stator-I2
  protection trips shortly afterwards. Manual attempts at 0.125/0.245 s are
  blocked by the persistent thermal latch, including after cooling below trip.
- `thermal_retry`: the same trip followed by ATS resets at 0.12/0.20 s.
  Both renewed ATS close attempts remain blocked by thermal memory.

The I2 replica is dimensionless, not a measured winding temperature. Its
post-trip equation is d(level)/dt = -level/tau with tau = 0.02 s here.
Physical generator/motor temperatures remain separate states. The benchmark
checks this analytic cooling law and retained latch over the entire saved
grid, physical continuity across events, copper-loss/heat consistency and
shaft/kinetic/thermal energy closure. No reset in this profile clears thermal
lockout. No automatic cooldown permission or field-thermal reclose is claimed.

Twenty qualification cases cover eight scenarios at two prescribed temperature
sets plus four list permutations. Radau/DAE at 2/1 ms give 80 native runs,
40 independently integrated SI trajectories and 160 comparisons. A separate
consumer re-integrates the reference, checks saved owners and comparisons,
initial spectra, clocks, refinement and sixteen permutation pairs. The consumer
checks 864 timing segments: maximum clock error 9.99e-16, native energy-integral
error 0.01495 J (0.1 J gate), independent energy error 1.60e-8 J (1e-4 J gate).
These are numerical consistency metrics. Expected
native aggregate counts: 24 thermal trips, 48 thermal close blocks, 112 forward
transfer starts, 16 PT transfer blocks and 16 completed returns.

This qualification has a 0.3 s horizon and deliberately permissive numerical
synchronism windows. It does not certify equipment or prescribe operating
settings. Several live islands, generator field-thermal reconnection,
joint standby heat initialization, primary close-failure fallback and arbitrary
simultaneous-root ordering remain separate work. Installed-wheel replay is
same-host Python-I with benchmark imports blocked, not independent-host
authentication. No GitHub or CI is used.

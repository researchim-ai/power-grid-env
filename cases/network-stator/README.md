# Generator-port OC, delayed breaker and I² protection

These authored cases combine generator stator R(T), physical generator/motor
heat, shaft/kinetic/thermal energy, TGOV1/SEXS limits, a definite-time network
overcurrent relay and a delayed generator breaker. Source and feeder remain
connected throughout. The independent SI reference integrates its own clocks,
roots and actuator queue with DOP853, without native event/current helpers.

```bash
python -m power_grid_env.cli run-study cases/network-stator/relay_first.json --out /tmp/network-relay-first.json
python -m power_grid_env.cli run-study cases/network-stator/thermal_first.json --out /tmp/network-thermal-first.json
python -m power_grid_env.cli run-study cases/network-stator/failed_open.json --out /tmp/network-failed-open.json
python -m benchmarks.network_stator_acceptance --out /tmp/network-qualified
python -m benchmarks.network_stator_verify --source /tmp/network-qualified --out /tmp/network-accepted
```

Use fresh output directories. Three race outcomes are checked:

- `relay_first`: 150 A pickup, 5 ms relay delay and 5 ms opening delay. The
  generator opens before its I² threshold. The physical temperature and I²
  state remain continuous; mechanical opening does **not** latch I² protection.
- `thermal_first`: 80 ms opening delay. I² protection disconnects the machine
  while the requested operation is pending. The later command reports
  `already_in_requested_state`, with **zero** breaker opening attempts.
- `failed_open`: the 5 ms actuator fails. After another 20 ms, the breaker
  failure check confirms current above 100 A. I² protection opens later. There
  is one failed mechanical attempt and no backup actuator in this profile.

All relay/heat/machine/controller settings are numerical stress-test assumptions,
inherited from the [stator](../generator-stator/README.md) and
[hybrid](../hybrid-stator/README.md) profiles. They are not recommended operating
settings or identified manufacturer parameters. The 0.5 s horizon is intentional.

The runtime fix makes an active, available definite-time relay advance at
`1 / time_multiplier_s` until its dropout guard acts. Retesting the pickup
threshold inside that continuous mode previously introduced a discontinuity at
the localized root: a 150 A crossing could evaluate as 149.99999999999997 A.
Both engines then failed on the 1 ms and 0.5 ms grids, despite completing on a
2 ms grid. It also incorrectly paused the clock in the pickup/dropout hysteresis
band. Tests cover equality, hysteresis, CT unavailability, reset and latching.
Inverse-time curves and the stateless IEC operate-time function are unchanged.

Eight cases cover passport/equilibrium temperatures, all three outcomes and
reversed command/relay/breaker lists for the thermal-first case. Radau and
network DAE, each on two grids, give **32 native runs, 16 independent trajectories
and 64 comparisons**. The consumer repeats every reference, checks pickup
current, relay and actuator delays, failure confirmation, attempt counts,
opening continuity, latch cause, analytical cooling, initial spectra and eight
permutation pairs. Event identity includes cause and breaker target; a wrong
deadline or counter fails acceptance.

Energy gates split all event boundaries: 0.1 J for saved native samples and
1e-4 J for independent dense quadrature. Observed maxima are 0.000563 J and
2.92e-6 J. Event refinement is below 8e-12 s. These are numerical consistency
measurements, not equipment accuracy or a global stability certificate.

Installed-wheel checks use Python isolation on the same host, with checkout and
reference imports blocked. This is local reproducibility, not independent-host
or authenticated delivery. No GitHub or CI is used.

Still outside this combined qualification: line/source opening, UV, islands,
backup coordination, ATS, reclose, faults, inverse-time generator-port R(T)
derivatives, PSS/GENROU and arbitrary simultaneous root ties. Existing separate
profiles do not imply that all these combinations have been qualified together.

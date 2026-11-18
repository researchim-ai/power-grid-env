# Unified stator R(T), heat, limits and generator I² trip

This profile combines electrical stator R(T), physical generator/motor heat,
TGOV1/SEXS limits and one latched generator I² trip in a single independently
integrated hybrid trajectory. The external source stays connected throughout.

```bash
python -m power_grid_env.cli run-study cases/hybrid-stator/immediate-release.json --out /tmp/hybrid-immediate.json
python -m power_grid_env.cli run-study cases/hybrid-stator/delayed-release.json --out /tmp/hybrid-delayed.json
python -m benchmarks.hybrid_stator_acceptance --out /tmp/hybrid-qualified
python -m benchmarks.hybrid_stator_verify --source /tmp/hybrid-qualified --out /tmp/hybrid-accepted
```

Use fresh output paths. Reference pulses begin at 0.05/0.25 s and are removed at
0.15/0.35 s. Valve bounds are 0.1/0.3 pu, field bounds 1/1.4 pu; governor R=0.5,
I² threshold=1.8 and time constant=0.05 s. All parameters are authored numerical
stress-test assumptions, not operational/OEM settings.

Two declared SEXS lead ratios exercise different cascades. With TATB=1, opening
while both controllers are held immediately releases SEXS: its measurement
switches from grid voltage to the disconnected machine's own terminal EMF.
With TATB=0.1, SEXS releases later; its lower limit may remain held at the 0.5 s
horizon. The independent engine keeps separate held/reference/topology contexts,
localizes both limiter and I² roots, opens the generator, and reevaluates held
free rates before restarting integration. Event history includes the causal
order `generator_thermal_trip` then `controller_limit_release` at the same time.

Opening removes both generator Norton admittance and injection. Copper current
and heat become zero; excitation/core heat, rotor motion and physical thermal
storage continue. The shaft/terminal/kinetic/thermal energy budget remains the
one declared in the [stator profile](../generator-stator/README.md). I² is a
separate dimensionless replica, not the physical temperature in C.

A runtime cache defect was fixed alongside this corpus. RMS-winding operator
keys now include motor/generator connection flags. Generator admittance deltas
are applied for fixed-R as well as stator-R(T) generators, so immediate queries
between discrete opening and normal factorization cannot reuse the connected
matrix. A reproducer previously showed about 0.104 pu complex-voltage discrepancy;
tests now compare immediate and freshly factorized results to roundoff. This
change concerns the RMS-winding operator; it is not a general arbitrary-topology
transaction API.

Six cases cover two initial-temperature policies, both lead ratios and reversed
command lists for the immediate-cascade cases. Both native engines and two
output grids yield 24 runs, 12 independent trajectories and 48 comparisons.
The consumer replays all references, checks 24 trips, 184 limiter events,
16 immediate trip/release cascades, held states, post-trip cooling, initial
spectra, command-order invariance, copper power/heat identity and energy integrals.

Energy quadrature splits every hybrid boundary. A short segment containing only
its two boundary samples uses a trapezoid instead of being discarded; longer
segments use Simpson quadrature. A synthetic linear-power test explicitly
covers a 50 microsecond segment. Gates remain 0.1 J native saved-grid and 1e-4 J
independent dense integral. Observed maxima are about 0.000370 J and 1.02e-6 J.
Event/power/energy-channel gates remain 2e-7 s, 0.003 W/var and 0.003 J.

No native trip, limiter, stator or energy helpers are used by the independent
reference. Installed-wheel replay runs locally on the same host. This corpus
includes no network OC/UV, line/source switching, faults, islands/reclose, PSS or
arbitrary simultaneous-root certification. Wider protection/topology studies
and public machine/thermal parameter anchors remain open. Numerical convergence
is not equipment accuracy or global hybrid stability qualification.

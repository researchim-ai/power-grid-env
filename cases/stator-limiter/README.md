# Electrical stator R(T), physical heat and TGOV1/SEXS limits

These authored pulse studies qualify upper/lower controller entry, hold and
release in `GENCLS_TGOV1_SEXS_STATOR_RT`. The generator stator resistance changes
the shared network with temperature; copper heat comes from its electrical
power drop, and only core heat receives an extra shaft debit. Motor winding
R(T), generator thermal storage and rotor kinetic energy continue to evolve
while a controller state is held.

```bash
python -m power_grid_env.cli run-study cases/stator-limiter/both.json --out /tmp/stator-limits.json
python -m benchmarks.stator_limiter_acceptance --out /tmp/stator-limits-qualified
python -m benchmarks.stator_limiter_verify --source /tmp/stator-limits-qualified --out /tmp/stator-limits-accepted
```

Use fresh output paths. Examples cover TGOV1 alone, SEXS alone and both. The
benchmark adds specified initial temperatures and reversed coincident-command
lists. Positive pulses start at 0.05 s and are removed at 0.15 s; negative pulses
start at 0.25 s and are removed at 0.35 s. The horizon is 0.5 s. Valve bounds are
0.1/0.3 pu, field bounds 1/1.4 pu; pulse sizes are 0.6 pu mechanical reference
and 0.3 pu voltage reference. These are authored numerical stress tests, not OEM
or operational prescriptions. No initial states or model parameters are tuned
during execution.

The independent SI model combines its own winding/network/PV/heat equations with
a separate root-localized limiter engine. It clamps an entering state, holds its
derivative at zero, and releases it when the free rate reverses. Reference and
held-mode contexts are saved per dense segment, preserving historical and
one-sided samples after later commands. No native limiter, stator, thermal or
energy numerical helpers enter reference integration.

Eight declared cases produce 32 native Radau/DAE runs at two output grids,
16 independent trajectories and 64 comparisons. The consumer replays every
reference and comparison, checks 192 native limiter events and 88 held reference
segments, compares initial spectra, validates electrical copper versus heat,
and checks eight command-permutation pairs. Controller name/bound and command
value/outcome are part of event identity. Energy is integrated separately on
every interval between hybrid events; kinetic plus thermal energy changes are
compared with the shaft/terminal/damping/ambient power budget. The shared
quadrature helper is hash-bound in the consumer receipt and local delivery.

Numerical gates: 2e-7 s event timing, 0.003 W/var power, 0.003 J energy channels,
1e-4 J independent dense energy integral and 0.1 J native saved-grid quadrature.
Observed native integral error on this corpus is about 0.0001433 J, independent
error about 1.23e-6 J. These are numerical metrics, not physical equipment
accuracy. Joint heat initialization uses the full physical spectrum; specified
initial temperatures use a frozen-temperature electromechanical subset.

Tests also check clipped/held stator and energy DAE products at a supplied
inconsistent voltage, complex Schur actions, exact command-order invariance,
historical reference contexts and rejection of wrong limiter metadata.

The unchanged R06.49 wheel is replayed locally on the same host. This profile
keeps the external source connected and OC/UV protection inactive. It includes
no faults, generator I2 trip, switching, islanding, reclose, PSS or field/EMT
energy storage. Combining all limiter/protection/topology mechanisms and public
parameter calibration remains a subsequent step; global hybrid stability and
arbitrary simultaneous roots are not certified.

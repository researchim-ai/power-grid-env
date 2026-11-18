# GENCLS + TGOV1/SEXS + motor winding R(T)

This explicit `winding_generator_profile: GENCLS_TGOV1_SEXS` profile couples
classical generator dynamics and motor winding heat through the shared network.
It supports TGOV1 alone, SEXS alone, or both, while the existing
`GENCLS_fixed_field` profile continues to reject controls.

TGOV1 has a droop demand, bounded valve, turbine lag/lead and speed damping.
SEXS measures terminal voltage and uses its filter/lead-lag and bounded field
output. Initial valve/turbine states, field/filter states and power/voltage
references are initialized from the solved operating point. An initial state
at a controller limit is refused. Parameters are authored assumptions, not OEM
passports or a general recommendation for real equipment.

Both `winding_state: passport` and `equilibrium` are supported. The former fixes
initial winding temperatures, which may immediately change; the latter solves
motor heat balance and checks the full physical spectrum including controls.
The spectrum has 12 states for one controller and 14 for both in these examples.
Passport-mode stability checks exclude the nonstationary heat states; they must
not be read as a full thermal stability certificate.

```bash
python -m power_grid_env.cli run-study cases/controlled-winding/both-fault.json --out /tmp/controlled.json
python -m benchmarks.controlled_winding_acceptance --out /tmp/controlled-qualified
python -m benchmarks.controlled_winding_verify --source /tmp/controlled-qualified --out /tmp/controlled-accepted
```

Use fresh output directories. The six examples cover the three controller
combinations, hold and finite balanced fault. Benchmarks additionally cover both
heat policies, Radau/DAE and two output grids: 12 authored physical cases,
48 native runs, 24 independent SI trajectories, 96 comparisons and six initial
refusals. Fault impedance is R=0.5 ohm / X=0.2 ohm, applied at 0.1 s and cleared
at 0.2 s. Motor and generator ratings are 75 and 100 kVA; generator initial
terminal targets are 20 kW / 0.96 pu.

Independent controller equations, winding/KCL, generator swing and P/V/heat
roots are implemented without native numerical helpers. The offline consumer
replays the references and refusals, compares spectra and checks all qualified
controller traces remain at least 1e-4 pu inside their output limits. Generator
power and winding heat comparison limits are 0.003 W/var.

Analytic tangents also have tests for clipped and held rows, eliminated voltage,
inconsistent algebraic voltage and complex Schur actions. These local derivative
tests **do not qualify limiter entry/release trajectories**. That requires a
separate hybrid reference. PSS, GENROU, generator heating, island/reclose studies
and broader topology validation remain outside this profile. No OEM or global
stability certificate is implied. OC pickups in these examples keep relays
inactive. Installed-wheel replay is local on the same host.

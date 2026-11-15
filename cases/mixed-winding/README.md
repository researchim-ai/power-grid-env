# Fixed-field GENCLS + two-cage RMS R(T)

These authored balanced radial cases combine a 75 kVA induction motor (two rotor
cages sharing one rotor temperature) and a 100 kVA classical synchronous machine.
Generator parameters and capability limits are assumed, not OEM data. The motor
starts from loaded electrical equilibrium at passport winding temperatures.
Initial generator P is 20 kW and terminal voltage is 0.96 pu; PV initialization
sets its internal EMF and mechanical input. After initialization excitation is
constant: this is **not an AVR**. Physical winding temperatures evolve, so this
initial condition is **not joint thermal equilibrium**.

- `hold.json`: 0.4 s without a programmed disturbance.
- `fault.json`: finite balanced shunt fault, 0.1–0.2 s, R=0.1 ohm, X=0.03 ohm.

```bash
python -m power_grid_env.cli run-study cases/mixed-winding/fault.json --out /tmp/mixed-trace.json
python -m benchmarks.mixed_winding_acceptance --out /tmp/mixed-qualification
python -m benchmarks.mixed_winding_verify --source /tmp/mixed-qualification --out /tmp/mixed-accepted
```

Use a fresh output directory. Both `radau` and `network_dae` are covered by the
benchmark. The opt-in `coupled.winding_generator_profile: GENCLS_fixed_field`
is mandatory. Controls, GENROU and generator thermal states are rejected. A separate
[joint motor heat/PV initialization profile](../mixed-equilibrium/README.md)
is now available using `winding_state: equilibrium`. Network automation clocks exist but pickups in these
examples deliberately keep OC relays inactive; this profile does not qualify
protection coordination, generator switching, islands or resynchronization.

The independent benchmark solves SI stator/rotor winding equations, nodal KCL,
generator swing equations and its own P/V/load root. Eight cases cover mutual
leakage 0/0.02 pu, initial temperatures 20/20 and 90/70 C, hold/fault. Each uses
both native engines at two output grids and two independent DOP853 tolerances.
Absolute generator power and heat comparison limits are 0.003 W/var. This is a
numerical cross-check on authored cases, not physical validation or superiority
against other simulators. The local wheel replay uses the same host.

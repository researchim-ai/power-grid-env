# Joint P/V/load/heat initialization: GENCLS + motor R(T)

This authored balanced radial profile solves motor speed, stator/rotor
**equilibrium temperatures**, network voltages and complex generator internal
voltage simultaneously. Generator terminal targets are 20 kW / 0.96 pu. Its
internal EMF magnitude and mechanical input are fixed after initialization.
Generator heating, AVR, governors and PSS are outside this profile.

The motor is a 75 kVA two-cage machine with one common rotor thermal node;
the GENCLS is 100 kVA. These parameters are assumed, not OEM data. A bounded
local multistart root is checked against original ODE/KCL, absolute heat-power
residuals, generator Q limits, I² replica limits and the full physical Jacobian.
This is a fixed-topology local stability check, not hybrid/global stability.

`coupled.operating_point.winding_state: equilibrium` selects this mode with
`winding_generator_profile: GENCLS_fixed_field`. Passport temperatures become
search seeds. On the hot-seed example the solved temperatures are approximately
57.908 / 46.664 C, speed 0.9888485 pu; these values need not match another circuit.
The generator has no thermal states: “joint” means its P/V and rotor constraints
are solved together with the motor electrical/mechanical/thermal equations.

```bash
python -m power_grid_env.cli run-study cases/mixed-equilibrium/hold.json --out /tmp/joint-hold.json
python -m power_grid_env.cli run-study cases/mixed-equilibrium/fault.json --out /tmp/joint-fault.json
python -m benchmarks.mixed_equilibrium_acceptance --out /tmp/joint-qualified
python -m benchmarks.mixed_equilibrium_verify --source /tmp/joint-qualified --out /tmp/joint-accepted
```

Use fresh output directories. The fault is 0.1–0.2 s, R=0.1 ohm, X=0.03 ohm;
these are finite balanced shunt-fault scenarios. Both native engines, two output
grids, two initial-temperature seeds, two mutual-leakage values and independent
SI winding/KCL/swing roots are checked: 32 native runs and 16 independent
trajectories. The offline consumer replays all references and eight refusals,
checks all ten physical eigenvalues and absence of drift in the hold cases.
Independent spectrum uses finite differences in SI flux coordinates; native
spectrum uses analytic derivatives in reduced RMS coordinates.

Tests additionally check a five-second hold and a mixed single/double-cage
motor group. These supplementary checks do not extend the independent benchmark
to arbitrary topologies or machine families. Relay pickups in the examples keep
OC protection inactive. Missing cooling paths, excessive temperature, generator
Q violations and inconsistent relay-replica policies produce explicit refusals.
Large heat capacity cannot hide an impossible heat balance.

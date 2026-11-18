# Energy-consistent lumped-loss GENCLS profile

`GENCLS_TGOV1_SEXS_ENERGY` supplies both grid output and the declared copper/core
heat from the shaft. The old `GENCLS_TGOV1_SEXS_HEAT` observer retains its original
one-way behavior. One heat node per generator is required in the new profile.

This closes the **declared rotor/thermal/port power budget**, not a full
field-resolved electromagnetic machine. The electrical GENCLS port still has
pure transient reactance; it has no armature resistance stamp. Copper loss is
an effective shaft load estimated from terminal current and the thermal R(T)
law. It does not cause a separate resistive voltage drop. Core heat uses the
same fixed-frequency quadratic terminal-voltage law as the observer.

```text
P_loss = P_copper + P_core
2 H S omega d(omega)/dt = P_shaft - P_terminal - P_loss - P_damping_exchange
C dT/dt = P_loss - G (T - Tambient)
E_kinetic = H S omega²
E_thermal = C (T - Tambient)
P_shaft - P_terminal - P_damping_exchange - G(T - Tambient)
    = d(E_kinetic + E_thermal)/dt
```

S is the rated VA power base used by the per-unit equations. TGOV1 output is
interpreted as constant shaft power in pu. Damping is the **signed** exchange
`D * (omega - 1) * S`, not an irreversible heat loss: it may be negative below
synchronous speed. Field/shaft torsional storage is not included. The new rotor
power equation requires positive speed and differs from the old nominal-speed
acceleration convention away from omega=1.

```bash
python -m power_grid_env.cli run-study cases/generator-energy/hold.json --out /tmp/energy-hold.json
python -m power_grid_env.cli run-study cases/generator-energy/fault.json --out /tmp/energy-fault.json
python -m benchmarks.generator_energy_acceptance --out /tmp/energy-qualified
python -m benchmarks.generator_energy_verify --source /tmp/energy-qualified --out /tmp/energy-accepted
```

Use fresh output paths. Joint heat initialization solves the electrical P/V and
motor/thermal conditions, then sets generator shaft power and TGOV1 reference
including losses exactly once. The authored hold example needs about 20.8837 kW
shaft power for 20 kW terminal output and 883.70 W heat removal. Specified initial
temperatures may be nonstationary. Analytic DAE actions include the loss gradient
and the speed denominator; invalid nonpositive speed is refused.

`g.energy.*` channels expose shaft/terminal power, heat input, ambient removal,
signed damping exchange, kinetic/thermal rates and stored energies, and the
instantaneous balance residual. Heat remains available as `g.heat.*`.

Four authored hold/fault cases cover two initial heat policies, both engines
and two output grids: 16 runs, eight independent SI trajectories, 32 comparisons,
and eight initial refusals. The offline consumer independently integrates power
between each pair of event boundaries, and also checks Simpson quadrature of
the saved native power channels against stored-energy changes. Gates are
1e-4 J for independent dense-output quadrature and 0.1 J for native saved-grid
quadrature; the latter is a sampling gate. Observed native error is about
0.000325 J on this corpus. Energy-channel comparisons use 0.003 J and power
comparisons 0.003 W/var; these numerical tolerances are not physical accuracy
claims. Derivative tests include inconsistent algebraic voltage, complex Schur
and off-nominal positive rotor speed.

Parameters remain authored assumptions. No OEM calibration, direct electrical
R(T), negative-sequence heating, field energy, combined trip/island/reclose or
limiter-crossing qualification is claimed for this profile. A future electrical
resistance stamp must move copper loss into internal electrical power and remove
its extra shaft debit to prevent double counting. Installed replay is local on
the same host.

# GENCLS electrical stator R(T), joint heat initialization and energy

`GENCLS_TGOV1_SEXS_STATOR_RT` replaces the pure-reactance generator port with
`Zg(T) = Rref * (1 + alpha * (T - Tref)) + j Xprime`, using the heat passport's
phase resistance in ohms and the machine's own per-unit base `Zbase=ULL²/Srated`.
Each generator requires a heat node. The existing observer and lumped-shaft-loss
profiles retain their previous semantics.

Both generator Norton admittance and source injection change with temperature.
The shared network operator and DAE products account for `Y(T)` and its
`Y_T * (E - V)` directional term. The stator current tangent includes
`dI=(dE-dV-I*dZ)/Z`; merely changing the saved loss channels is insufficient.

```text
P_internal = P_terminal + P_copper
2 H S omega domega/dt = P_shaft - P_internal - P_core - P_damping_exchange
C dT/dt = P_copper + P_core - G(T-Tambient)
```

Copper heat now comes from the internal-to-terminal real-power drop. **Only
core loss is added as an extra shaft debit**. This avoids charging copper twice.
The integral rotor/thermal energy budget remains the one declared in the
[energy profile](../generator-energy/README.md). Signed damping is external power
exchange; field/torsional/EMT energy storage is outside this reduced RMS model.

```bash
python -m power_grid_env.cli run-study cases/generator-stator/hold.json --out /tmp/stator-hold.json
python -m power_grid_env.cli run-study cases/generator-stator/fault.json --out /tmp/stator-fault.json
python -m benchmarks.generator_stator_acceptance --out /tmp/stator-qualified
python -m benchmarks.generator_stator_verify --source /tmp/stator-qualified --out /tmp/stator-accepted
```

Use fresh output paths. Joint initialization adds generator temperature to the
motor speed/temperature and complex internal-voltage unknowns. Its analytic
reduced Jacobian includes stator admittance and all generator heat-balance rows.
Original ODE/KCL, watt residuals, initial Q limits and the full physical spectrum
are checked. A bounded local multistart search is not a uniqueness or global
nonexistence proof. Passport motor-temperature mode requires **specified**
generator temperature; choose joint equilibrium to solve it instead.

`g.stator.*` exposes phase resistance in ohms/pu, internal power, electrical
copper loss and internal-minus-terminal-minus-copper residual. `g.heat.*` and
`g.energy.*` expose thermal and total energy accounts. Initial generator internal
voltage and phase now include the resistive drop. In the authored stationary
example terminal output is 20 kW and required shaft power remains about 20.8837 kW.

Four authored hold/fault cases cover two initial policies, both engines and two
output grids: 16 native runs, eight independent SI trajectories and 32
comparisons. The independent reference solves its own winding/KCL/PV/heat root
with a resistive generator port. The consumer replays all references and six
initial refusals, checks equality of electrical copper loss and heat input,
physical spectra and pointwise/integral energy budgets. Tests also cover a hot
operator perturbation, inconsistent-voltage DAE actions, complex Schur, the
analytic reduced root Jacobian and the zero-loss temperature boundary.

These are assumed parameters, not OEM passports. Numerical tolerances are
2e-7 s for scheduled events, 0.003 W/var for power, 0.003 J for energy channels,
1e-4 J for independent dense quadrature and 0.1 J for saved native-grid quadrature.
Observed native integral error on this corpus is about 0.000328 J. This is not
physical equipment accuracy. Qualification covers controller interiors and
finite balanced faults with the external source connected. Combined stator
R(T)/trip/reclose/island/limiter-crossing profiles, saturation, negative sequence,
rotor/field hotspots and calibrated public machine data remain open. Installed
replay is local on the same host.

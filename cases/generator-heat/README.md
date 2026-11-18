# Dimensioned one-node generator thermal observer

`winding_generator_profile: GENCLS_TGOV1_SEXS_HEAT` adds a generator temperature
state to the existing controlled GENCLS / motor winding R(T) study. It is
separate from the dimensionless stator I² protection replica.

**This is a one-way thermal observer.** Its heat losses are not debited from
GENCLS electrical or shaft power; temperature does not change the electrical
network resistance. It therefore does not close the machine's total
mechanical/electrical/thermal energy balance. The observer's own thermal energy
balance is checked. This limitation is also explicit in `generator_heat_model`
in the result JSON.

The model uses phase RMS current in amperes and terminal voltage in pu:

```text
Pcu = 3 * Rref * (1 + alpha * (T - Tref)) * |I_phase_A|²
Pcore = core_loss_w * |Vterminal_pu|²
C * dT/dt = Pcu + Pcore - G * (T - Tambient)
```

C is J/K, G is W/K and Rref is ohms per phase. The single temperature represents
an effective mean thermal node; it does not resolve stator/rotor hotspots or
coolant circuits. The quadratic core-loss law assumes the profile's fixed
frequency approximation. No speed/frequency-dependent iron-loss law is implied.
All example parameters have explicit authored provenance and are **not OEM data**.

```bash
python -m power_grid_env.cli run-study cases/generator-heat/hold.json --out /tmp/heat-hold.json
python -m power_grid_env.cli run-study cases/generator-heat/fault.json --out /tmp/heat-fault.json
python -m power_grid_env.cli run-study cases/generator-heat/specified.json --out /tmp/heat-specified.json
python -m benchmarks.generator_heat_acceptance --out /tmp/heat-qualified
python -m benchmarks.generator_heat_verify --source /tmp/heat-qualified --out /tmp/heat-accepted
```

Use fresh output paths. Joint motor heat initialization requires generator heat
`initialization: equilibrium`; the generator temperature is found from its
closed-form heat balance, followed by original RHS and watt-based residual
checks. Positive stable equilibrium requires `G > 3 Rref alpha |I_phase_A|²`.
A nonpositive denominator, out-of-range equilibrium or inconsistent initial
policy is refused. Runtime temperature validity is checked without clipping;
there is no temperature-trip guard in this model.

For the authored hold example the generator starts near 64.1848 C, with copper
heat about 699.38 W, core heat 184.32 W and ambient heat removal 883.70 W.
Capacity changes the thermal pole, not the equilibrium temperature. Specified
mode starts at 80 C and need not be stationary.

Four hold/fault scenarios cover both heat policies, Radau/DAE and two output
grids: 16 native runs, eight independent SI trajectories, 32 comparisons and
eight initial refusals. Independent code contains its own SI heat equation and
stationary calculation and never calls the native heat helpers. Analytic DAE
products, inconsistent-voltage residuals, complex Schur actions and physical
spectra are checked. Specified-temperature stability checks deliberately freeze
all nonstationary heat states; joint equilibrium uses the full physical spectrum.
A separate 600 s constant-input test checks the closed-form temperature response
and integrated thermal energy; this is not a 600 s network qualification.

The independent corpus covers controller interiors, no generator trips and no
topology changes. Combined heat/trip/island profiles, electrical R(T) feedback,
shaft-loss feedback, OEM calibration and wider topology validation remain open.

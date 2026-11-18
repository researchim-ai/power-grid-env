# Independent generator I²-trip and post-opening trajectory

This authored balanced radial profile extends the existing GENCLS / TGOV1 /
SEXS / two-cage motor R(T) model with an independently localized permanent
opening of the generator by its stator I² replica. The external source remains
connected; this is not islanding or resynchronization.

```bash
python -m power_grid_env.cli run-study cases/generator-trip/passport.json --out /tmp/trip-before-fault.json
python -m power_grid_env.cli run-study cases/generator-trip/equilibrium.json --out /tmp/trip-during-fault.json
python -m benchmarks.generator_trip_acceptance --out /tmp/trip-qualified
python -m benchmarks.generator_trip_verify --source /tmp/trip-qualified --out /tmp/trip-accepted
```

Use fresh output paths. The specified-replica case starts at 0.2 with threshold
0.3 and time constant 0.05 s, opening around 0.00713 s. The equilibrium case starts
from solved current squared with threshold 1.0; a finite balanced fault at
0.1–0.2 s induces opening around 0.10373 s. These example times depend on the
authored parameters. The governor droop R=0.5 is declared explicitly for this
corpus, keeping controller outputs away from limits; no tuning is done at run
time. Parameters are assumptions, not OEM passports or operational settings.

The independent SI network removes **both** the generator admittance and Norton
injection after the event. Its stator current is zero thereafter. Generator
rotor and controller states continue to evolve; SEXS measures the machine's
separated terminal EMF instead of the energized grid bus. Motor winding flux,
speed and physical temperatures remain continuous while algebraic network
voltages/currents may change. The latched replica then obeys
`theta(t) = threshold * exp(-(t - t_trip)/tau)`.

Six studies cover TGOV1, SEXS or both and specified/equilibrium replica policies.
Both native engines and two output grids yield 24 runs, 12 independent
trajectories and 48 comparisons. The offline consumer replays all references,
compares one-sided event samples and checks zero generator current/power,
state continuity and 252 post-trip exponential samples. Event tolerance is
2e-7 s, generator power and winding heat tolerance 0.003 W/var.

The independent engine uses no native thermal-trip, controller, current or
network-stamping helpers. It only models one permanent generator opening, finite
scheduled faults and inactive OC/UV protection; limiter crossings, other topology
commands, reclose and islands are outside its scope. The stator I² state remains
a **dimensionless protection replica**, not physical generator temperature.
Installed-wheel checks run locally on the same host, with the R06.45 runtime
unchanged. No OEM, arbitrary topology or global hybrid stability qualification
is implied.

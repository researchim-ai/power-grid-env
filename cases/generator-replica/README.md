# Generator stator I² replica with motor winding R(T)

Use `winding_generator_profile: GENCLS_TGOV1_SEXS_I2` to combine the existing
GENCLS/TGOV1/SEXS model, physical motor winding temperatures and a generator
stator current-squared protection replica. Other mixed profiles still reject
`generator_thermal`, so old input contracts are preserved.

**The generator state is a dimensionless I² replica, not temperature in C.**
Its equation is `dtheta/dt = (|I_generator_pu|^2 - theta) / tau`. It does not model
generator copper/iron losses, thermal conductances, hotspots or R(T). Motor
winding temperatures remain physical two-node states as before. No OEM thermal
capability or generator-temperature accuracy is claimed.

```bash
python -m power_grid_env.cli run-study cases/generator-replica/hold.json --out /tmp/replica-hold.json
python -m power_grid_env.cli run-study cases/generator-replica/trip.json --out /tmp/replica-trip.json
python -m benchmarks.generator_replica_acceptance --out /tmp/replica-qualified
python -m benchmarks.generator_replica_verify --source /tmp/replica-qualified --out /tmp/replica-accepted
```

Use fresh output paths. Joint motor heat equilibrium requires replica
`initialization: equilibrium`; the initial level is solved current squared.
Passport motor-temperature mode also permits a specified replica level. Initial
levels at/above the trip threshold are refused. The full physical spectrum has
15 states in the two-cage/both-controller example, including the replica pole.
Analytic variable-Y DAE actions include its current and state derivatives.

Four authored hold/fault scenarios cover both initialization policies, Radau/DAE
and two output grids: 16 native runs, eight independent SI trajectories and
32 comparisons. Independent equations include the replica dynamics and initial
level. The public independent corpus stays below trip. Six initial refusals
check an unsupported flag, initial trip and inconsistent equilibrium policy.

`trip.json` separately exercises a latched stator-replica trip and generator
current extinction in both native engines. These checks and installed replay
**do not independently qualify trip timing or the post-trip waveform**. Extending
the external hybrid reference to generator disconnection remains a separate
step. The physical generator heat model also remains open.

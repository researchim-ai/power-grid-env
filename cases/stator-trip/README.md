# Combined stator R(T), physical heat, I² protection and generator opening

These authored studies independently qualify one permanent generator opening
in `GENCLS_TGOV1_SEXS_STATOR_RT`. Both temperature-dependent electrical stator
resistance and the dimensioned heat node remain active, alongside the separate
stator I² protection replica. The external source stays connected; this is not
an island, reclose or automatic-transfer study.

```bash
python -m power_grid_env.cli run-study cases/stator-trip/before-fault.json --out /tmp/stator-before.json
python -m power_grid_env.cli run-study cases/stator-trip/during-fault.json --out /tmp/stator-during.json
python -m benchmarks.stator_trip_acceptance --out /tmp/stator-trip-qualified
python -m benchmarks.stator_trip_verify --source /tmp/stator-trip-qualified --out /tmp/stator-trip-accepted
```

Use fresh output paths. Specified initialization starts the I² replica at 0.2
with threshold 0.3, opening around 0.00713 s, before the fault. Joint equilibrium
initialization uses solved temperatures/current squared and threshold 1.0; the
fault at 0.1–0.2 s produces opening around 0.10377 s. The time constant is 0.05 s.
These are numerical stress-test settings, not OEM protection settings.
`fixed-field.json` retains TGOV1 but has no SEXS, giving a constant excitation
amplitude for an analytic post-trip temperature check. Governor droop R=0.5 is
an explicit authored input, not tuned during execution.

Opening removes both the generator Norton admittance and injection. Current,
terminal electrical power and copper heat become zero. Rotor, controller,
winding-temperature and I² states remain continuous. The I² state decays
exponentially to zero, while physical temperature generally **does not** cool
as an unpowered body: the excited rotating machine still has core heat.
Without SEXS, excitation amplitude and the declared core loss are constant,
so temperature approaches `Tambient + Pcore/G` with pole `-G/C`. With SEXS,
core heat follows the machine's isolated terminal EMF. The controller must not
measure the live grid bus after its generator is disconnected.

The shaft/port/kinetic/thermal energy budget from the
[stator R(T) profile](../generator-stator/README.md) is checked through the event.
After opening, copper loss is zero, core heat still loads the shaft, and the
signed damping exchange remains an external power term. Field/EMT storage and
frequency-dependent core losses are outside this reduced model.

Six cases cover TGOV1, SEXS or both and both initialization policies: 24 native
Radau/DAE runs at two output grids, 12 independent SI trajectories and 48
comparisons. The independent engine localizes its own I² crossing and removes
its own generator branch. The consumer replays every reference and one-sided
event sample, compares initial spectra, verifies threshold metadata, copper
power/heat identity, state continuity, 252 post-trip replica cooling samples
and 24 native integral energy balances. It also verifies the independent dense
power integral against kinetic/thermal energy changes.

Tests include connected/open DAE products with the I² and physical heat states,
absence of a temperature-dependent ghost stamp after opening, and the constant
core-heat temperature solution. No native trip, current, stator or energy
helpers are used in the independent reference. The unchanged R06.49 wheel is
replayed locally on the same host.

Fixed numerical gates: 2e-7 s events, 0.003 W/var power, 0.003 J energy channels,
1e-4 J independent dense integral, 0.1 J native saved-grid quadrature. Observed
native integral error on this corpus is about 0.0012 J. These are numerical
metrics, not equipment accuracy. All machine/thermal/protection parameters are
authored assumptions. Wider switching, OC/UV interaction, limiter crossings,
islands/reclose, negative-sequence heating and OEM calibration remain open.

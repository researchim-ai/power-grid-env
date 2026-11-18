# TGOV1/SEXS limiter entry, hold and release with motor R(T)

These authored radial studies independently qualify a bounded hybrid extension
of the existing `GENCLS_TGOV1_SEXS` profile. They exercise upper and lower bounds
of TGOV1, SEXS, or both. The generator remains coupled to a two-cage induction
motor with stator/rotor winding temperatures and R(T).

At 0.05 s a positive controller-reference pulse is applied; at 0.15 s it is
removed. A negative pulse starts at 0.25 s and is removed at 0.35 s. The study
ends at 0.5 s. TGOV1 valve limits are 0.1/0.3 pu; SEXS field limits are 1/1.4 pu.
Pulse sizes are 0.6 pu mechanical reference and 0.3 pu voltage reference. These
are authored numerical stress tests, not recommended operating commands or
OEM parameters.

```bash
python -m power_grid_env.cli run-study cases/limiter-winding/both.json --out /tmp/limiter-trace.json
python -m benchmarks.limiter_winding_acceptance --out /tmp/limiter-qualified
python -m benchmarks.limiter_winding_verify --source /tmp/limiter-qualified --out /tmp/limiter-accepted
```

Use new output directories. `governor.json`, `exciter.json`, and `both.json` use
joint initial motor heat equilibrium. The benchmark additionally covers fixed
initial passport temperatures and reversed source lists of coincident commands.
`event_policy: coincident_roots_v1` and the full event trace are required for
this qualification. Initial conditions remain inside the controller bounds.

An independent DOP853 engine localizes entry roots, clamps only the active state,
holds its derivative at zero and releases when the free derivative reverses.
Release may be immediate after a command or later without any command: the SEXS
lower-bound release in the combined case occurs around 0.4524 s. Each dense
segment stores its own references and held-mode context, preserving historical
and one-sided samples after subsequent commands. No native limiter/controller
helpers or native event times enter the independent integration.

Eight scenarios produce 32 native Radau/DAE runs at two output grids, 16
independent trajectories and 64 waveform/event comparisons. The offline consumer
replays both reference tolerances, checks 192 native limiter events, held states,
pre/post-event samples and eight command-permutation pairs. Event identity
includes controller name, bound and command value; merely matching event counts
is insufficient. Event tolerance is 2e-7 s, power/heat tolerance 0.003 W/var.

Qualification covers these finite, authored pulse sequences. It does not prove
arbitrary simultaneous-root handling, global hybrid stability, relay/topology
interaction, PSS/GENROU, generator heating or OEM correctness. OC protection is
kept inactive and no faults/switching are included. Local installed-wheel replay
uses the already qualified R06.43 runtime unchanged, on the same host.

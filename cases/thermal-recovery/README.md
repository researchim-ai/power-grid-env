# Explicit stator-I2 reset, reconnection and repeated thermal trips

This extends [initial PT faults and thermal lockout](../standby-interlocks/README.md)
with explicit operator acknowledgement. The source, GENCLS, two-cage motor,
R(T), TGOV1/SEXS and heat/energy models retain their previous authored passport
and hot temperature sets. This is a bounded numerical profile, not OEM settings.

Opt in on a `generator_thermal` passport with `reset_level`, strictly below
`trip_level`. Without it, the old permanent-latch behavior and serialization
are unchanged. The new command takes the generator ID and no value:

```json
{"time_s": 0.105, "kind": "generator_thermal_reset", "equipment_id": "g"}
```

Reset requires a configured generator actuator, a stator-I2 passport with an
explicit reset threshold, a physically disconnected generator, and a replica
level at or below that threshold. It clears only the stator-I2 latch. It never
sets the replica to zero, changes physical temperature, closes the breaker,
clears operator inhibition or acknowledges a separate field-thermal latch.
The recorded thermal opening time still enforces minimum open duration.
DAE discrete-mode identity includes protection latches even while disconnected.

Eight scenarios, two temperature sets and four command/equipment permutations
form 20 cases: 80 Radau/DAE runs on 2/1 ms grids, 40 independent SI DOP853
trajectories and 160 comparisons over 0.3 s.

- `hot_reset`: request at 0.085 s is too early; the later close remains locked out.
- `connected_reset`: request at 0.0801 s is rejected while connected.
- `manual_retrip`: reset at 0.105 s preserves heat memory. An early close fails
  by angle; a later dead-receiver close succeeds and causes another thermal trip.
- `ats_retrip`: reset followed by ATS rearming; reconnection again trips thermally.
- `minimum_open`: the first post-reset close is blocked by minimum open time,
  measured from the thermal trip; a later close succeeds and retrips.
- `close_deadline`: reset at the exact delayed-close deadline precedes queue
  execution. Closing is admitted through existing checks and then retrips.
- `operator_inhibit`: reset does not clear manual isolation; ATS closing is
  blocked. A later explicit manual close clears inhibition only after applying.
- `double_reset`: the second acknowledgement is a no-op and retains heat memory.

The second trip is an expected adverse result. Clearing a relay memory flag
does not correct the overloaded physical state. These cases use permissive
test synchronism settings, including a 0.5 pu live/dead threshold, to exercise
reconnection and retrip. They are not operating recommendations. Default
synchrocheck thresholds have not been relaxed.

```bash
python -m power_grid_env.cli run-study cases/thermal-recovery/manual_retrip.json --out /tmp/retrip.json
python -m benchmarks.thermal_recovery_benchmark --out /tmp/recovery-qualified
python -m benchmarks.thermal_recovery_benchmark --source /tmp/recovery-qualified --out /tmp/recovery-accepted
```

Use fresh output paths. The consumer re-integrates references and recomputes
comparisons, cooling/clock integrals, continuous physical states at event sides,
latch transitions, manual inhibition, event order and sixteen full permutation
pairs. Expected native totals: 144 thermal trips, 64 applied/16 blocked/8 no-op
resets, 144 applied/48 blocked closes and 104 forward transfers. The 1024 clock
segments have maximum error 7.78e-16. Native energy error is 0.004152 J against
0.1 J; reference error is 3.46e-9 J against 1e-4 J. These measure numerical
consistency, not field accuracy. Several live islands, field-thermal reset,
arbitrary root ties and joint standby heat initialization remain outside scope.

## Diagnostic tool episodes for learning

The qualified evidence can produce 18 pre-command diagnostic records. Only
current connected/latched/I2 indications and the declared reset threshold are
exposed. Tool replies contain no future events or expected answer. Scripted
teacher episodes call `inspect_generator` and `read_reset_policy`, then return
a structured diagnosis. `submit_decision` records a decision without executing
a physical action. Strict scoring rejects changed thresholds and unknown fields.

```bash
python -m power_grid_env.recovery_curriculum build --qualified /tmp/recovery-qualified --accepted /tmp/recovery-accepted --out /tmp/recovery-corpus
python -m power_grid_env.recovery_curriculum export --root /tmp/recovery-corpus --expected-manifest "MANIFEST_FROM_BUILD" --out /tmp/recovery-sft.jsonl --kind sft
python -m power_grid_env.recovery_curriculum export --root /tmp/recovery-corpus --expected-manifest "MANIFEST_FROM_BUILD" --out /tmp/recovery-prompts.jsonl --kind prompts
```

Replace the manifest placeholder with the owner-accepted hash printed by build.
The Python API `score(root, task_id, answer, expected_manifest)` returns a strict
0/1 diagnostic reward. It loads its own anchored observation; the candidate
cannot supply replacement criteria or a replacement physical state.

All records belong to one topology family and remain **train-only**, including
both temperature sets. Eighteen records are a small diagnostic seed, not eighteen
independent network families. There are no images, model training, held-out
evaluation or closed-loop GRPO control results in this delivery. Multimodal
observations, larger disjoint families and actual Qwen learning remain open.

`benchmarks.thermal_recovery_learning` builds and replays both exports, validates
tool messages and positive/wrong/malformed decisions. The same standalone script
runs with Python-I against an installed wheel while blocking benchmark imports;
source and installed exports must match bitwise. This is same-host reproducibility,
not authenticated external validation. No GitHub or CI is used.

# Physically verified LLM data, SFT and GRPO

The first end-to-end LLM task profile is synthetic feeder restoration: unbalanced
OpenDSS quasi-static physics, a disclosed three-phase fault, constant PQ loads,
instantaneous switches, fresh telemetry, an 80 s horizon and 10 s decisions.
Motor dynamics, EMT and converter studies are not yet exposed as LLM tasks here.

The generator varies rooted feeder topology, impedances, phase loads, opaque IDs
and private fault resistance. Cases include available backup, insufficient backup
capacity and a locked tie. Isomorphic trees cannot cross splits within a corpus;
parameter changes do not create independent topologies. Across separate shards,
keep the same family-to-split registry yourself.

SFT exports cover complete plans, observation-conditioned actions and tool-call
conversations with actual simulator responses. No fabricated reasoning is added.
The teacher is the best feasible of three public-observation policies, not a global
optimum. Private tasks and physical journals remain outside model datasets.
Semantic verification replays generation, teacher selection, actions and traces.

GRPO candidates reset to the same state. Safe reward combines physically
recomputed served energy (0.75), restoration (0.25) and actual operations (−0.001).
Unsafe/invalid outputs receive −1; solver failures invalidate the batch rather
than becoming training labels. Short plans advance through the horizon.
The TRL callback returns raw rewards; TRL computes its own group advantages.
Constant-reward groups have zero advantages.

`rollout_tools` supports public observe/what-if/act/finish conversations with
strict JSON and budgets. The direct TRL GRPO recipe trains whole plans; a native
multi-turn tool-policy optimizer remains future work. This is a text-data boundary
on a trusted training host, not a sandbox for arbitrary Python policies.

From the repository root with the package installed:

```bash
power-grid llm-data generate --input cases/llm-training/generation.json --out reports/llm-corpus
power-grid llm-data verify reports/llm-corpus
power-grid llm-data export reports/llm-corpus --split train --kind plans --out reports/sft-public
power-grid llm-data export reports/llm-corpus --split train --kind prompts --out reports/grpo-public
```

Default: 12 tasks, 6/3/3 train/dev/test across four topology families. Each corpus
has task, topology-size and artifact-byte limits. Use bounded shards for volume;
more electrical variants do not mean more independent topologies.

`score-group` consumes `{task_id, completions}` with 2–32 JSON-string plans.
`evaluate` consumes JSONL `{task_id, completion}` covering the entire dev/test
split exactly once. All output directories must be new:

```bash
power-grid llm-data score-group reports/llm-corpus --input /path/group.json --out reports/group
power-grid llm-data evaluate reports/llm-corpus --input /path/predictions.jsonl --out reports/test
```

For local training, use the optional `.[llm-training]` stack in a separate
environment. Tested direct versions are in `requirements/llm-training-tested.txt`;
this is not a complete dependency/CUDA lock. Replace checkpoint paths in the
provided recipes; downloads, remote code and external tracking are disabled:

```bash
python -m power_grid_env.llm_fit --input cases/llm-training/sft-local.json
python -m power_grid_env.llm_fit --input cases/llm-training/grpo-local.json
python -m power_grid_env.llm_inference --input cases/llm-training/evaluate-local.json
```

A checkpoint needs a chat template. Context overflow fails before training;
network descriptions and labels are never silently truncated. SFT uses completion
loss without packing. GRPO uses TRL on-policy generation without vLLM, beta=0.
The checked profile is single-process local execution. Checkpoint weights,
tokenizer identities, source identities, versions and training logs are recorded.
Held-out inference reports all tasks, including invalid and unsafe completions.

Tests: `python -m pytest tests/test_llm_training.py`. Local acceptance:
`PYTHONPATH=. python benchmarks/llm_data_acceptance.py --out reports/llm-acceptance`.
Optional `benchmarks/llm_cpu_smoke.py` runs real SFT/GRPO optimizer plumbing on a
tiny whole-message-token GPT2 bandit. It is not a useful trained LLM or evidence of
engineering reasoning. Corpus acceptance uses the same Engine as its teacher;
independent numerical qualification belongs to the separate simulator suite.

Further work includes motor-start tasks, delayed/partial observations, a multi-turn
GRPO optimizer, larger independent topology holdouts and a real instruct-model
comparison. No trained-model quality or superiority over other simulators is claimed.
See the [full Russian guide](LLM_TRAINING_RU.md) for exact limits and semantics.

# Current learning target: Qwen3.5-0.8B

The next stage is now recorded in [R06.64 adapter assessment and schema curriculum](QWEN35_08B_EVAL.md).
The R06.63 results and future-work wording below describe the original training release.

The owner selected **Qwen/Qwen3.5-0.8B** after the initial 4B processor work.
The official [model card](https://huggingface.co/Qwen/Qwen3.5-0.8B) describes the
native multimodal conditional-generation model. The local copy is already
available; no download is required on this host. The 4B metadata and R06.62
receipts remain historical evidence, not the active training configuration.

`requirements/qwen35-08b-local-target.json` pins the local config, tokenizer,
chat template, image/video preprocessing files, shard index and all weight
shards. Its owner SHA is
`e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262`.
The single BF16 source shard is 1,746,942,600 bytes; its SHA is
`04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696`.
The locally recorded origin revision is not independently authenticated.
Integrity checks do not authenticate the model publisher.

## Environment

Use a separate environment from the legacy Transformers 4.x/TRL recipe:

```bash
python -m venv .venv-qwen35-08b
.venv-qwen35-08b/bin/python -m pip install '.[qwen35-training]' \
  -r requirements/qwen35-08b-training-tested.txt
```

The local run reused existing Python 3.12 package installations read-only
through a project-local venv `.pth`: Qwen's Transformers 5.22.1/Torch 2.7.0
environment first, simulator dependency packages second. Neither external
environment nor original model files were modified. This local reuse is not a
portable environment lock; the commands above describe a separate installation.
The measured profile is **CPU float32 / four threads**. GPU execution, BF16
training and full-model tuning are outside this qualification.
CPU reference kernels are used for gated-delta operations. Training and base
evaluation ran concurrently; their recorded wall times are reproducibility
diagnostics, not isolated performance measurements or speedup claims.

Use these environment variables for fully local execution:

```bash
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export CUDA_VISIBLE_DEVICES=''
```

## Preflight and adapter SFT

```bash
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_local_run.py preflight \
  --target-path requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262
```

The runner verifies every pinned file before loading native safetensors, uses
`local_files_only=True`, `trust_remote_code=False`, and never imports model
Python code from the checkpoint. Model loading is explicit; the simulator does
not require Torch or model weights for ordinary studies.

The bounded SFT profile adds rank-four, alpha-eight LoRA to the final decoder
block's q/k/v/o and gate/up/down projections: **100,352 trainable parameters**.
Vision and other backbone parameters stay frozen. Images still pass through the
native vision encoder; gradients update the selected decoder adapters. Eval mode
keeps dropout disabled during this deterministic adapter profile; it does not
disable autograd. Native output logits are selected only at causal positions
predicting assistant tokens. This avoids allocating vocabulary logits for the
entire image prompt. Tests compare the selected mean CE and gradients with the
full shifted masked loss, including assistant end tokens.

The two-step qualification plan is `qwen35-08b-adapter-plan.json`. The one-epoch
plan `qwen35-08b-train-epoch.json` visits all 36 train-image observations once in
the fixed corpus order; there is no shuffle, augmentation or dev-based selection.
Both start from the original checkpoint. They are separate runs, not optimizer
resume. Learning rate is 0.0002, gradient clipping 1.0, seed 42.
The current plan is capped at 64 optimizer steps; it is a bounded local training
profile, not a long-running distributed training framework.

```bash
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_local_run.py train \
  --target-path requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --export-root reports/design-multimodal-exports-current-2026-11-16/train-image \
  --export-manifest e7ed876f8015a44a83f994ceb6461708c83e037d3d3634ff2e089bac77a4b267 \
  --plan cases/design-multimodal/qwen35-08b-train-epoch.json \
  --out reports/my-qwen35-08b-epoch
```

Output paths must be fresh. Training rejects held-out prompts and non-train SFT,
invalid indices, nonfinite losses/gradients/updates, zero gradients and silent
no-op updates. It verifies the frozen backbone hash and absence of frozen
gradients, saves adapter tensors using safetensors, destroys in-memory adapter
values and restores them, then requires exact replay of native logits/loss.
The final receipt binds target, data, code, hyperparameters and actual steps.
An interrupted run has no successful sealed receipt. Optimizer/RNG resume,
distributed training, padded multi-example batching and a general LoRA exporter
to third-party runtimes are not implemented by this profile.

`benchmarks/qwen35_adapter_replay.py` additionally starts a fresh model process
under Python `-I`, blocks test/reference imports and checks an owner-pinned
training artifact. Source and installed-wheel replay must emit identical
selected-logit safetensors hashes and the original trained loss.

## Base model evaluation

```bash
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_local_run.py evaluate \
  --target-path requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --collection-root reports/design-multimodal-exports-current-2026-11-16 \
  --collection-manifest 041263d35e10c94d2be9c211c7d5e69f4d6f4767731c2a7362cb49767b754344 \
  --corpus-root reports/design-multimodal-qualified-current-2026-11-16 \
  --corpus-manifest 79f4eae6580c6318060c16f74c0c9c272b18d5fcf20953665db62f887f74b31b \
  --split dev --plan cases/design-multimodal/qwen35-08b-generation-plan.json \
  --out reports/my-qwen35-08b-base-dev
```

The evaluator uses the **original unadapted model** and covers all twelve dev
observations in all four modalities: 48 generations, greedy decoding, equal
96-token output budgets. No answers enter model inputs. Complete per-modality
denominators are mandatory; malformed, fenced, truncated, duplicated-key and
otherwise invalid answers receive zero and stay in the denominator. Partial
predictions are saved for diagnosis but cannot produce a successful sealed
evaluation. Reward concerns displayed motor indications only. It is not a
network-design, switching or simulator-comparison score.

Train examples have two motors and dev examples have three; motor count and
topology change together. This small profile cannot isolate a pure topology
generalization effect. `benchmarks/qwen35_prediction_audit.py` replays every
official score and records schema/JSON/indication failures separately. Removing
code fences is examined only as a diagnostic; it never changes official scores.

The training receipt's last-example loss is a training-tract check, **not a
held-out accuracy measurement**. This evaluator does not load trained adapters.
The next acceptance stage must add an owner-anchored adapter evaluator, compare
base and trained checkpoints on the same complete dev protocol, then freeze the
training recipe before opening test. No test examples are used in these runs.
Physics-scored closed-loop GRPO, motor-family holdouts and independent engineer
evaluation remain open. The existing indication reward callback alone does
not qualify those stages.

Artifacts live under ignored `reports/qwen35-08b-*`; model weights, adapters,
local environments and generated reports are not source files for git.

## Local results (R06.63)

Both the two-step qualification and the full 36-step image-only train epoch
completed. The original backbone hash stayed unchanged. For each checkpoint,
source and installed-wheel fresh processes produced identical complete result
manifests and selected logits. No optimizer state is required or claimed by
these inference replays.

The unadapted model completed all 48 dev generations: **0/12 exact matches in
each modality, 0/48 overall**, zero schema-valid decisions and 39 output-budget
exhaustions. These are retained negative results for the current prompt/decoder
contract. They do not isolate visual perception or measure the trained adapter.
The complete failure audit preserves original scores. The test split remains
unused for model generation and optimization.

All **285 targeted regressions** passed without failures, errors or skips,
including 39 new tests; a subset of 52 tests also passed in the native model
stack. Sixty-nine native processor packs passed from the installed wheel.
The next model-quality gate is the full base-versus-adapter dev comparison.

# Qwen3.5-0.8B: mixed-view SFT and update-boundary resume

This stage uses the **separate response-schema protocol** prepared in R06.64.
The public prompt explicitly lists static motor IDs and the JSON response
contract. It adds no current-state labels. Results must not be compared against
raw-prompt R06.63/R06.64 accuracy as if only the weights had changed.

## Fixed recipe

Owner profile: `qwen35-08b-mixed-profile.json`. It pins the local target metadata,
the immutable curriculum collection and the private host grading corpus.
Train has 36 physical observations, each represented as image, text, combined
and schematic: **144 views, not 144 independent physical observations**.
Dev has 12 observations × 4 views; test is not exported or evaluated here.
Shared equipment and the confounding between topology and motor count remain.
Train-only coverage audit finds39 `at_running_speed` and33 `below_running_speed`
motor labels per measured view, **zero `disconnected` labels**, and two motors
in all36 observations. Schematic has36 refusal targets. Supply loss does not
itself disconnect a motor. This recipe cannot demonstrate learning all three
indication classes. Add physically simulated contactor opening cases in new
train families; do not synthesize disconnection by editing labels.

- Original model: local Qwen/Qwen3.5-0.8B, CPU float32, four threads, eval mode
  (dropout disabled; adapter autograd remains enabled).
- Frozen vision encoder/backbone; LoRA rank4, alpha8 on the seven final decoder
  projections, **100,352 trainable parameters**. No full-model tuning.
- Seed42, one complete shuffled epoch, learning rate0.0002, AdamW with
  betas0.9/0.999, eps1e-8, zero weight decay, `foreach=False`, clip norm1.
- Four single-example microbatches per effective batch: **36 optimizer updates**.
  A dedicated CPU `torch.Generator` constructs a deterministic permutation of
  all observation/view pairs. Global initialization/RNG consumption cannot
  change that schedule. The schedule hash is pinned in every checkpoint.
- Native variable-size image processing remains per example. No ad hoc image
  padding/collation, image substitution, truncation or text conversion of visual
  measurements. Prompts, pixels and padding never contribute to SFT loss.
- Each microbatch backpropagates its **assistant token sum**. Gradients are
  divided once by the actual assistant-token count of the entire effective
  batch, then clipped once. This includes short final groups. Averaging the
  per-example means instead would give short answers disproportionate weight.
- Train includes schematic refusal targets (`insufficient_observation=true`,
  empty `motors`) as well as indication reading. The diagnostic class rule,
  labels and strict grader are unchanged.

## Checkpoints and continuation

Every completed optimizer update creates a **new sealed directory** under
`checkpoints/update-NNNNNN`. Existing directories are never overwritten.
The parent run is sealed only after successful end checks. A crash can leave
an unsealed parent; already sealed child checkpoints can still be owner-pinned
and used for continuation. Ignore incomplete/unsealed child directories.

Each checkpoint contains:

- `adapter.safetensors`: trainable adapter tensors only;
- `optimizer.safetensors`: named FP32 AdamW step, first and second moments;
- `rng.safetensors`: CPU Torch RNG state;
- `receipt.json`: producer fingerprint, dependency versions, target and data
  anchors, exact plan/schedule, frozen backbone hash, consumed cursor and all
  effective-batch/microbatch metadata, losses and token denominators;
- `manifest.json`: complete bounded file inventory with hashes.

There is **no pickle/torch.load**. Resume validates all fields against the
current owner profile and producer, then restores named moments, adapter values
and RNG. It rejects a different seed, order, accumulation, learning rate,
checkpoint target, data, runtime, history or tensor inventory. Restart is at an
optimizer boundary with zero gradients. A group interrupted halfway is replayed
from the previous checkpoint; partial microbatch accumulation is not serialized.

Deterministic algorithms and pinned versions qualify this CPU profile. Exact
continuation is tested in a fresh installed-wheel process against the continuous
native run. This is a **same-host** check, not a cross-platform bitwise guarantee.
Python/NumPy RNG and CUDA RNG are not used by this trainer and are not saved.
No scheduler, dropout, mixed precision or random augmentation is supported by
this profile. Adding any of those requires extending the saved state and tests.

## Commands

Run from the repository root. Every output path below must be fresh. The local
`.venv-qwen35-08b` reuses the already available read-only Qwen stack and simulator
dependencies; it is a convenience environment, not a portable dependency lock.
No download, GitHub, CI, commit or push is required.

```bash
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CUDA_VISIBLE_DEVICES='' \
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_mixed_study.py train \
  --profile cases/design-multimodal/qwen35-08b-mixed-profile.json \
  --out reports/my-mixed-train
```

For a deliberate pause, use `--stop-after-updates 1`. Continue with the **same
profile**, a fresh parent output, the sealed child path and its owner-trusted
manifest SHA256 (not the parent manifest):

```bash
... benchmarks/qwen35_mixed_study.py train \
  --profile cases/design-multimodal/qwen35-08b-mixed-profile.json \
  --resume reports/my-mixed-train/checkpoints/update-000001 \
  --resume-manifest <owner-checkpoint-manifest-sha256> \
  --out reports/my-mixed-continued
```

Omit `--stop-after-updates` to finish the originally scheduled epoch; that option
is an absolute update cursor, not an additional update count or changed recipe.
`evaluate` accepts only a **complete** training receipt. A partial checkpoint
cannot silently be reported as a completed epoch.

```bash
... benchmarks/qwen35_mixed_study.py base \
  --profile cases/design-multimodal/qwen35-08b-mixed-profile.json \
  --out reports/my-schema-base-dev

... benchmarks/qwen35_mixed_study.py evaluate \
  --profile cases/design-multimodal/qwen35-08b-mixed-profile.json \
  --training reports/my-mixed-train \
  --training-manifest <owner-complete-training-manifest-sha256> \
  --out reports/my-schema-adapter-dev

... benchmarks/qwen35_mixed_study.py compare \
  --profile cases/design-multimodal/qwen35-08b-mixed-profile.json \
  --base reports/my-schema-base-dev --base-manifest <owner-base-manifest-sha256> \
  --adapter reports/my-schema-adapter-dev --adapter-manifest <owner-adapter-manifest-sha256> \
  --out reports/my-schema-paired-dev
```

The `...` prefix above means the same offline interpreter/environment as the
first command. Full dev evaluation retains all 48 raw generations, invalid JSON
and token-budget exhaustions; no answer repair or selected successes. Both
models get identical schema prompts/pixels and greedy96-token generation.
The old raw baseline is rejected by the paired comparator because the collection
anchor/protocol differs. Private grading frames/targets remain host-only;
only individual public exports should be exposed to an agent.

## Boundary and next work

This is indication reading, not network design, safe switching, healthy-operation
classification or a GRPO training loop. More train steps do not qualify physical
models or establish superiority over another simulator. Freeze a recipe before
opening test; do not relabel old held-out topologies into train. Next work is
motor-count-matched independently qualified train cases including explicit contactor
opening/closing and threshold-boundary coverage, explicit constrained
output control as a separate protocol, visual grounding ablations and tool-use
rollouts with host-owned physics rewards.

Measured R06.65 results and exact evidence anchors are recorded below, in
`plans/CURRENT_STATUS.md` and the immutable delivery receipt.

## Prepared early-observation extension (not trained)

`early-train-profile.json` pins the original corpus and independently audited
source/acceptance artifacts. `design_train_early_observations` re-audits the
stored source studies and uses the unchanged `public_frame`/renderer/diagnosis
on cutoffs **0.0, 0.2, 0.399 s**, only in existing train topology families.
The last cutoff is exactly representable by the stored0.5ms output grid and
reads0.399s in the PNG's three-decimal heading; it is not visually rounded into
the0.400s post-closing observation. Initial single-point and pre-closing plots
were visually inspected. No simulation is rerun and no labels are hand-edited.

There are27 additional time-prefix observations:63 total,252 SFT views and48
unchanged dev prompts. These still come from **nine original train study
traces**, not63 independent cases. Per measured view, motor labels are39 running,
78 below-running and9 disconnected; schematic has63 refusal targets. All motors
remain on the original two-motor train projects; disconnected examples are m2
before its scheduled first close, not arbitrary-ID or rotating-contactor-opening
cases. Motor-count coverage, naming biases and contactor-opening dynamics still
need new independently checked cases.

Owner profile for this **prepared, untrained** data:
`qwen35-08b-early-profile.json`. The current epoch/evaluation uses the original
144-view `qwen35-08b-mixed-profile.json`; the new data does not retrospectively
enter that experiment. Source and installed processor checks cover all300 packs
(252 train masks+48 dev prompts) without loading weights. The installed runtime
rebuilds the complete extension byte-identically. Public dev conversations and
PNG bytes, and every original dev/test frame/target, are checked against the
previous owner-pinned corpus. No test export or test model generation is done.

```bash
PYTHONDONTWRITEBYTECODE=1 /home/user/miniconda3/bin/python -m benchmarks.design_train_early_observations \
  --profile cases/design-multimodal/early-train-profile.json \
  --out reports/my-fresh-early-train
```

Final prepared artifact: `reports/qwen35-early-train-final-2026-11-17`.
The earlier same-date intermediate directory without `-final` used0.3995s;
its visual time heading rounded to0.400s. It remains sealed historical evidence,
is not the prepared profile and was never used for training. Coverage v2 calls
time-prefix records **distinct observations**, explicitly not statistically
independent samples.

## Measured R06.65 outcome: semantic quality gate failed

The fresh mixed adapter completed **144 microbatches,36 optimizer updates**.
All frozen weights stayed unchanged; all36 update checkpoints validate. Native
installed continuation update1→2 reproduces adapter/AdamW/RNG tensors, files,
history and manifest bitwise. Four predetermined installed generation replays
(first dev index of each modality) reproduce text, tokens and scores, including
incorrect answers. This is a same-host subset, not a full independent-host run.

On all48 identical schema-protocol dev prompts, base scores0/48 and adapter
**9/48**:image/text/combined3/12 each,schematic0/12. Adapter outputs are48/48 valid
generic decisions and48/48 valid public response contracts, with no budget
exhaustion; base has17/48 valid decisions and7 exhaustions. Neither structural
metric establishes correct indication reading or appropriate insufficiency.
Motor-field matches are12/36 image,13/36 text,12/36 combined; the joint0/1 reward
is unchanged and all12 schematic answers incorrectly claim motor states.

**46/48 answers match the all-running constant policy**; only two distinct
strings are generated. Post-hoc public-static-ID controls, without opening any
image or reading measurements, score all-running9/48, all-below-running15/48,
all-disconnected0/48. The adapter therefore does **not** demonstrate semantic
improvement over even an uninformed class prior. Numeric control remains12/12
for text,combined and schematic,0/12 for image (no OCR). Controls are explicitly
post-hoc diagnostic policies, not trained models or neural compute comparisons.
No reward relaxation, answer repair, successful-subset selection or test use.
The older raw-prompt12/48 result is a different experiment and cannot be used as
a weight-only comparison against this9/48.

Before another long training run, qualify fixed train-generation probes with
assistant labels removed, numeric-reading/refusal and modality controls. The
train2-motor/dev3-motor discrepancy is a demonstrated confound, not a proven
single cause of collapse. New three-motor cases must stay in train topology
families and receive independent physics/numerical checks. Prepared early252
views fill a class-coverage gap, but do not establish new model quality and do
not fix motor-count/ID/rotating-disconnection coverage by themselves.

Evidence anchors:

- Complete training `reports/qwen35-mixed-train-2026-11-17`:
  `9725e7280b420430719d3c02872bcbc1d757c2a5f33ec3e0805085409275cd99`.
- Schema base `reports/qwen35-mixed-base-dev-2026-11-17`:
  `2ddd1bcb008e368cafcd375ce79f368099d312ab1f96645e938abb99adf28b7f`.
- Mixed adapter `reports/qwen35-mixed-adapter-dev-2026-11-17`:
  `237f7a86a4efac6df3cb9452f04f9e7ab7357c0bc4e718584a4460aee8f646e5`.
- Prepared extension `reports/qwen35-early-train-final-2026-11-17`:
  `2d41fe8412f2e3353c378c114744a614fee5c4f826a6a4ba5b04abc42b4eca06`.
- Current wheel SHA256:
  `a444f647a00c5f53f319147b026f32c757ab7f64e2ecfb88a8bd3742c72deffc`.

All **400 unique targeted regressions** pass (60 new);135 unique tests also pass
in the native stack. The delivery independently regrades all source predictions,
checks all checkpoints, proves original dev/test frames and public dev inputs
unchanged, verifies300 early native packs per runtime and the complete installed
data rebuild, and snapshots the wheel, code/docs/profiles and test reports.
Training/integrity gates passed; the **semantic quality gate remains failed**.
VIS-01/LEARN-01 remain in progress; EVAL-01 and closed-loop physics GRPO stay open.

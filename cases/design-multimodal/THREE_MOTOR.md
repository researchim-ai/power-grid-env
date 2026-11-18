# R06.66: train diagnostics, three motors on two sites and corpus v2

This stage **does not train another model**. It diagnoses the R06.65 near-constant
policy, adds new physically checked train cases and prepares data without
changing the old held-out observations. The target remains Qwen3.5-0.8B.

## What the learning controls establish

`qwen35_train_probe.py` selects the first train observation with all-below,
mixed and all-running targets **before inference**, then uses those three IDs
in each of four modalities. Public train prompts are projected from the pinned
SFT export by dropping the assistant message; all original public blocks and
images stay exact. No correct answers enter generation. These are **12 seen
training probes**, not an estimate of full train accuracy or generalization.

The model scores1/3 in image,text,combined and0/3 schematic (3/12 total). Thus
failure is present on seen examples too; motor-count/topology OOD alone cannot
explain the measured behavior. No model parameters, requirements or rewards
were changed. Test was not used.

Four additional probes (three text targets and one schematic refusal) compare
the full teacher-forced first-assistant-token logit with prompt-only logits and
two generated tokens with/without cache. Maximum absolute deviations are below
2.8e-5, against a predeclared1e-3 FP32 limit; greedy tokens match. This only
checks these prefixes and **two decoding steps**, not arbitrary full decoding.
It does not establish that every potential training/backend issue is absent.

Token diagnostics distinguish JSON/ID syntax from flag/class-value spans.
On these text examples format NLL is about0.015–0.016 and semantic NLL0.130–0.359.
For the refusal target, the single semantic flag has NLL1.201 while format NLL
is0.026. Averaging over syntax masks the wrong decision. Next training work
should measure/weight semantic tokens and require seen-case reading/refusal
controls before a long run; simply expanding data or starting sparse GRPO does
not demonstrate a fix.

These neural diagnostics explicitly use the historical producer3c4… installed
runtime of the trained artifact. Current corpus software has producerb82…;
no historical solver code is executed by the new data verifier.

## New physics profile

Two new **P3 train-family** projects have three single-cage motors on the same
three-node network: m at n1, m2 and m3 at n2. This is newly simulated colocated
load wiring, not the old four-node dev/test projects renamed as train. The
n2 owner demand covers both75kVA motor passports (150kVA). Existing catalog,
criteria and uncertainty declarations remain unchanged.

Each project has six4s cases:

- Simultaneous start, staggered start and source-voltage loss/recovery.
- Open/reclose m, m2 and m3 separately at3.0/3.2s.

The star is accepted under its declared engineering criteria; the chain is
rejected, including its upstream ampacity check. Both produce completed,
numerically qualified physical trajectories. A rejected design is **not**
a positive engineering-design label; displayed-indication targets are separate.
All six motor openings occur above0.9pu speed. After opening, current is zero
while shaft speed/flux continue; the correct indication is `disconnected` even
when displayed speed remains above the running threshold.

**36 native runs** cover2 designs×6 cases×3 output/max-step grids. Existing
shaft-energy, algebra, I²t, thermal and decision-stability checks pass without
relaxing thresholds. Independent SI nodal screening and **six new OpenDSS
passive three-phase fault solutions** agree with the compiled catalog network.
No motor fault contribution or general fault/EMT qualification is implied.

`three_motor_si_reference.py` independently solves the complex SI node-voltage,
stator-current and rotor-current circuit. It integrates rotor flux, shaft speed
and the passive current-squared thermal replica with DOP853. It does not call
production stamps, motor constants, RHS or integrator. Initial flux/speed are
zero; thermal level comes from the passport.12 continuous reference trajectories
cover all36 native grids and eight channels per motor. Maximum errors:

- Speed:3.87e-10pu; current:6.12e-9pu; voltage:8.99e-11pu.
- EMF components:3.08e-9pu; thermal replica:1.86e-10.
- Active/reactive power:0.000514W /0.000442var.

The reference is additionally checked against the full steady T circuit, exact
open-motor fan coasting/flux/thermal decay and node-order invariance. Scope is
balanced diagonal passive lines, constant-Z auxiliary loads, one Thevenin source,
single-cage fan drives and prescribed switching with nontripping thermal
replicas. No saturation, winding R(T), automatic-trip logic, OEM accuracy or
arbitrary topology/model-family qualification is claimed.

All36 source trajectories replay **bitwise**, including axes/events/channels,
from the installed wheel on the same host with the original BLAS environment.
The first trial forcibly changed BLAS thread settings and failed strict replay;
it was rejected, not admitted by increasing tolerances. Final matched replay is
in six separately sealed sibling batches to preserve existing artifact budgets.
No cross-BLAS or independent-host bitwise guarantee is made.

## Multi-revision corpus and prepared data

The old traces have physics producer5ec… and new ones3c4… . **Corpus v2** carries
a bounded exact `physics_code_sha256s` inventory and preserves each frame's
actual producer. Export v2 carries per-record producer metadata and its exact
selected inventory. It does not attribute historic traces to the current data
exporter. Corpus/export v1 stays supported and cannot silently opt into a mixed
inventory. Unknown/missing/duplicate/unused producers are rejected; static-ID
response enrichment preserves v2 lineage. Producer metadata is not model input.

The old early extension has63 train prefixes. The new12 study traces add96
prefixes at0,.2,.399,.4,1.5,1.6,3.0 and4s:

- **159 train prefixes,636 SFT views**,48 unchanged dev prompts.
- 63 two-motor and96 three-motor prefixes in the same train topology family.
- Per measured view:112 running,273 below-running and29 disconnected motor
  labels; schematic159 refusal targets.
- These are correlated prefixes of **21 train study traces**, not159 independent
  networks. Motor-count coverage improves, but train/dev equipment and cases
  remain small and authored.

Every prior row/target/frame/PNG is unchanged. Public dev conversations and
pixels stay identical; test is not exported, enriched, generated on or used for
training. The new dataset is **prepared_not_trained**. Source and installed
runtime each pass all684 native processor packs (636 assistant-only masks+48
unlabeled dev prompts, max2978 tokens), and the installed runtime rebuilds the
complete corpus/raw exports/schema curriculum byte-identically. A rotating
post-opening three-motor waveform was visually inspected for readable OFF,
nonzero speed and zero current indications.

## Reproduction

Run from the repo root, always using fresh outputs. No GitHub/CI or Git mutation.
The source physics and SI artifacts were created with producer3c4…; changing
runtime means re-running those stages or explicitly auditing their pinned
historical revisions. Existing `source_inventory` performs data-only historical
source audits. See the sealed delivery for exact source/replay/data producer pins.

```bash
PYTHONDONTWRITEBYTECODE=1 /home/user/miniconda3/bin/python -m benchmarks.three_motor_training \
  --corpus reports/design-multimodal-qualified-current-2026-11-16 \
  --corpus-manifest 79f4eae6580c6318060c16f74c0c9c272b18d5fcf20953665db62f887f74b31b \
  --out reports/my-fresh-three-motor-physics
```

Run the independent reference and acceptance on owner-pinned source outputs
before creating training data. For the already qualified local artifacts:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/user/miniconda3/bin/python -m benchmarks.three_motor_dataset \
  --profile cases/design-multimodal/three-motor-data-profile.json \
  --out reports/my-fresh-three-motor-data
```

Prepared target/data profile: `qwen35-08b-three-motor-profile.json`.
This is a candidate data profile, not a newly qualified training recipe or
accuracy result. Raw teacher loss and two-step cache controls are in
`reports/qwen35-likelihood-probes-2026-11-17`; seen generation controls in
`reports/qwen35-train-probes-2026-11-17`. New physics/SI/data evidence and the
425-test/160-native-test gate are bound in
`reports/three-motor-delivery-2026-11-17/delivery.json`.

Next: implement and verify semantic-token loss metrics/weighting, class/refusal
sampling and matching resume semantics; run a bounded training pilot against
seen-case and numeric/class-prior controls, then full frozen-protocol dev. Keep
test closed. Engineering tool actions and physics-scored GRPO follow after
reading/refusal works. Broad DES-02/03/04 gaps remain open.

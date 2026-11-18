# Adapter evaluation and response-schema curriculum (R06.64)

The current target remains Qwen3.5-0.8B. This stage evaluates the original
36-step image-only adapter from R06.63. It does not train another model or open
test. See [training/environment setup](QWEN35_08B.md) and the pinned
`qwen35-08b-adapter-eval-profile.json` for exact owner data/model anchors.

## Reproduce the raw-prompt comparison

Run locally, with fresh output paths:

```bash
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export CUDA_VISIBLE_DEVICES=''
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_adapter_study.py evaluate \
  --profile cases/design-multimodal/qwen35-08b-adapter-eval-profile.json \
  --out reports/my-qwen35-adapter-dev
```

The profile pins the historical training producer explicitly. The default API
requires the current producer; an old artifact does not pass by merely claiming
to be compatible. Preflight verifies the sealed training receipt, original
model config/weights, train export, private corpus lineage, actual train-step
IDs and finite training rows. Model load then checks the frozen backbone hash,
adapter target modules/count, tensor keys/shapes/dtype/finite values and adapter
bytes. Historical Python never executes. Only the adapter safetensors values
are restored into the current native model.

All twelve dev observations run in image/text/combined/schematic modes. Raw
messages, images, processor, CPU float32, four threads, greedy decoding and the
96-token output budget match R06.63. There is no prompt rewrite or answer repair
in this evaluation. Generation kwargs and prediction metadata are also checked
against the unchanged base evaluator in a unit test. New and historical wheels
are compared to establish that the original generation/training modules did
not change. The cached baseline is reused with its explicit owner manifest and
producer pin; it is not silently rerun with a different protocol.

After retaining the resulting manifest SHA in trusted owner configuration:

```bash
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_adapter_study.py compare \
  --profile cases/design-multimodal/qwen35-08b-adapter-eval-profile.json \
  --adapter reports/my-qwen35-adapter-dev \
  --adapter-manifest OWNER_ADAPTER_EVALUATION_SHA256 \
  --out reports/my-qwen35-paired-dev
```

Comparison replays every strict reward against the host-owned corpus. It checks
complete paired task sets, model/data/decode identities, prompt token counts,
image counts, output budgets, validity flags and metric denominators. Missing,
duplicated, foreign, selectively scored or protocol-mismatched rows fail before
writing a successful comparison. Diagnostics separate invalid schema, missing
or unexpected motor IDs, incorrect indications and incorrect insufficiency.
The criteria and all-or-nothing reward stay unchanged.

The first dev observation in each modality is predetermined for installed-wheel
generation replay. The four raw texts, token counts and scores must match the
corresponding source run. This is a bounded same-host check, not a full48 replay
or an independently operated host.

## Separate response-schema curriculum

```bash
.venv-qwen35-08b/bin/python -I benchmarks/qwen35_adapter_study.py curriculum \
  --profile cases/design-multimodal/qwen35-08b-adapter-eval-profile.json \
  --out reports/my-qwen35-response-curriculum
```

This creates eight independent public exports: four train SFT exports with36
rows each and four dev prompt exports with12 rows each. No test export is
created, and direct enrichment of test is rejected. Every observation gains one
text block specifying the exact JSON object, allowed class enum, static motor
IDs and the conditional requirement for empty `motors` when observations are
insufficient. IDs come from the static passport; no measured value, expected
class, hidden state, future event or final design verdict is inserted.

Image bytes, existing public blocks and SFT answer strings remain unchanged.
Only static IDs and an explicit response contract are added. This supplies more
text grounding of identifiers in image mode; it must not be described as the
same prompt protocol as the original image-only benchmark. A distinct
collection manifest binds the new protocol. Repeated enrichment is forbidden.
The existing train-only label boundary remains enforced; dev has no labels.

All **192** enriched examples were packed with the actual local Qwen processor:
144 assistant-only loss masks and48 unlabeled dev prompts, at most2978 tokens.
The same checks ran from an installed wheel and rebuilt an identical complete
curriculum. These checks loaded no neural weights and performed no training.
The current 36-step adapter was evaluated only on the original raw inputs.

The bounded R06.63 trainer supports single-example updates and at most64 steps;
it cannot train a full144-example mixed-modality epoch unchanged. The next
learning stage needs explicit modality mixing, reproducible ordering, batching/
accumulation and a training checkpoint/resume protocol, then new base-versus-
adapter dev evaluation under the schema prompt protocol. Freeze that recipe
before opening test. GRPO/control/design qualification remains separate.

## Interpretation

This is a small authored diagnosis benchmark: shared motor models/catalog,
three topology families, two motors in train and three in dev. Count and
topology therefore change together. Correct indication reading is not network
design, protection coordination, long-horizon operation or normative approval.
Any measured difference is specific to the declared dev protocol. The test
split, general engineering-agent ability and simulator superiority stay open.

Reports and local model artifacts remain under ignored `reports/`; no GitHub,
CI, staging, commit or push is part of this workflow.

## Local results

The raw-input comparison completed all48 pairs. Base: **0/48**. The image-only
36-step adapter: **12/48** (25%): image2/12, text5/12, combined5/12,
schematic0/12. All48 adapter replies are schema-valid, with no output-budget
exhaustions; the base had no schema-valid replies and39 exhaustions. These
are observed differences on this particular dev protocol, not general agent
qualification or proof that visual reasoning improved.

Diagnostics find incorrect indications in10 image answers,6 text answers and7
combined answers. One text answer omits a motor. Every schematic answer fails
the required abstention and supplies motor states despite absent current
indications. Thus format adherence improved while reliable state reading and
insufficiency detection remain open. None of these failures was repaired.

`benchmarks/qwen35_public_numeric_baseline.py` adds an arithmetic control using
only the public instrument JSON. It obtains12/12 in text and combined, and
abstains without numeric text: schematic12/12, image0/12. The image result is
a declared lack of OCR, not a visual baseline. This control uses no private
targets as policy inputs and makes no neural compute-budget comparison.
The adapter still underperforms this simple control on numeric inputs.

All340 targeted tests passed (336 main regressions plus4 nonoverlapping public
policy tests), including55 new tests. A subset of90 also passed in the native
model environment. Four predetermined installed generation replays matched
raw text, tokens and scores exactly. The prepared schema curriculum has no
new trained model or accuracy result; it is the input to the next stage.

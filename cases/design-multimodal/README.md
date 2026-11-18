# Motor-study observations for Qwen3.5

**Current training target: Qwen3.5-0.8B.** See the
[local training and evaluation recipe](QWEN35_08B.md). The original R06.62
processor qualification below used 4B; its observations also support 0.8B.
R06.64 adds [paired adapter evaluation and a separate response-schema curriculum](QWEN35_08B_EVAL.md).

This profile turns already qualified network-construction studies into portable
multimodal observations. It adds no solver equations and executes no new physics
integrations. The owner pins both source manifests in `sources.json`; all 45
stored three-grid runs are re-audited against that explicitly pinned producer.
Default engineering verification still requires the current producer revision.

## Observation and target

Five designs, three scenarios and four observation times produce **60 frames**:
0.4, 1.5, 1.6 and 4 s, with the post-event side selected at discontinuities.
Each frame has a single-line diagram, a selected-equipment passport and a
voltage/current/speed waveform ending at the observation time. The waveform
retains extrema and event sides. No future sample, scheduled command, final
verdict, reward or hidden dynamic state enters public observations.

The corpus contains 62 deduplicated PNG assets. Displayed values are rounded to
0.001 pu. The target is deliberately narrow: identify each motor as
`disconnected`, `at_running_speed` or `below_running_speed`, using the displayed
connection indication and the displayed speed threshold of 0.900 pu. A coasting
motor can meet that speed threshold during a supply outage. This target does
not establish healthy operation, sufficient voltage, design feasibility or
permission to switch equipment.

Four modalities share the same physical frames:

- `image`: diagram, passport, waveform; no numeric state duplicated in text.
- `text`: numeric indications and selected-equipment facts; no images.
- `combined`: the three images and numeric indications.
- `schematic`: diagram and passport only; the correct response is
  `{"insufficient_observation":true,"motors":{}}`.

Topology families use an exact relabel-invariant undirected multigraph key.
Three-node star and chain descriptions are isomorphic and remain together in
train. The four-node star is dev; the four-node chain is test. Splits contain
36/12/12 observations. These are only three small topology families sharing
motor models and equipment catalog; they are not held-out motor families or a
measurement of VLM generalization. The qualified physics profile remains
400 V, 3/4 nodes, 2/3 single-cage motors and a four-second horizon.

## Build and export

Install `power-grid-env[vlm-data]` for Pillow/Matplotlib rendering. Single-line
rasterization also requires system librsvg and Cairo. From the repository root:

```bash
python - <<'PY'
from power_grid_env.design_multimodal import build
from power_grid_env.study_artifacts import read_json
sources = read_json('cases/design-multimodal/sources.json')
print(build(**sources,
    protocol=read_json('cases/design-multimodal/protocol.json'),
    out='reports/my-design-observations'))
PY
```

Output paths must be fresh. Record the resulting manifest SHA in the owner's
configuration; never obtain a trusted expected hash from candidate-controlled
data. Use that value in place of `OWNER_CORPUS_SHA256`:

```bash
python -m power_grid_env.design_multimodal export \
  --root reports/my-design-observations \
  --expected-manifest OWNER_CORPUS_SHA256 \
  --split train --kind sft --mode image --out reports/my-train-image
python -m power_grid_env.design_multimodal export \
  --root reports/my-design-observations \
  --expected-manifest OWNER_CORPUS_SHA256 \
  --split test --kind prompts --mode image --out reports/my-test-image
```

SFT export is restricted to train. Dev/test exports contain prompts only.
An export directory contains relative image paths, messages, receipt and
manifest; it can be moved independently. **Expose only an individual export to
an agent.** Corpus frames, targets, source studies and acceptance/replay parent
directories are private host evidence. A content manifest verifies integrity;
it provides neither authentication nor filesystem isolation.

## Native processor and diagnostic rewards

The `qwen35-processor` extra is separate from the existing `llm-training`
Transformers 4.x profile. Do not install both profiles in one environment.
The native check used Transformers 5.22.1, Torch 2.7.0, Pillow 10.4.0 and
tokenizers 0.23.3. `requirements/qwen35-local-target.json` records the local
checkpoint and owner-pinned config SHA; weights are never opened by this API.

```python
from power_grid_env.study_artifacts import read_json
from power_grid_env.vlm_batches import VLMExportDataset, local_qwen35_processor

target = read_json('requirements/qwen35-08b-local-target.json')
processor = local_qwen35_processor(target['local_checkpoint'], target['config_sha256'])
data = VLMExportDataset('reports/my-train-image', 'OWNER_EXPORT_SHA256')
batch, metadata = data.pack(processor, 0)
```

Packing uses the native chat template with thinking disabled. It checks exact
prompt/full token-prefix agreement, finite visual tensors and image-token
presence. Prompt, image and padding tokens receive label -100; only assistant
tokens contribute to SFT loss. Oversized examples fail instead of silently
truncating. Held-out prompts have no labels. This supplies a single-example
packing API, not a trainer, padded multi-example collator or qualified backward
pass.

```python
from power_grid_env.design_multimodal import IndicationGrader

grader = IndicationGrader('reports/my-design-observations', 'OWNER_CORPUS_SHA256')
rewards = grader.reward_batch(
    completions=generated_answers, task_ids=host_owned_task_ids, mode='image')
```

The host fixes task IDs and modality. The callback accepts plain JSON strings
or single assistant messages, returns exact-match 0/1 rewards and rejects extra
prose, duplicate keys, nonfinite numbers and malformed output. Targets are
validated once and cached. This is a diagnostic reward callback suitable for
integration into grouped sampling; it is **not qualified closed-loop GRPO for
network control or design**.

## Reproduction and remaining work

`benchmarks/design_multimodal_acceptance.py` verifies the full inventory,
12 exports, 240 positive and 480 negative grading cases. With `--rebuild`, it
requires an identical corpus manifest. `--installed-dir` supports the same
checks with an installed wheel under Python `-I` and blocked reference imports.
`benchmarks/design_vlm_processor.py` packs all 60 image observations and nine
additional modality/split examples using the actual local processor: 39 SFT
loss-mask checks and 30 held-out prompt checks, at most 2757 tokens and three
images in the measured matrix. No weights, forward pass or training ran.
`benchmarks/design_multimodal_delivery.py` binds evidence, source snapshots,
wheel boundaries and 246 non-skipped targeted regressions (40 new).

R06.63 adds actual 0.8B adapter forward/backward, train-only SFT and a complete
base-model dev evaluator; see the linked recipe for measured results and scope.
The remaining stages include base-versus-adapter held-out comparison and a
locked test assessment. Broader equipment/topology families, tool-use rollouts
and physics-scored design actions remain separate work. No model improvement
or advantage over another simulator is established by processor checks.

Mixed-modality training and safe optimizer/RNG continuation: [QWEN35_MIXED.md](QWEN35_MIXED.md).

Three-motor train physics, independent SI reference and mixed-revision corpus: [THREE_MOTOR.md](THREE_MOTOR.md).

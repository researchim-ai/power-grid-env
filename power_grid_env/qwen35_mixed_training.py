"""Deterministic mixed-view CPU SFT with data-only, update-boundary resume.

Microbatches remain single observations: variable image grids are never padded
or concatenated incorrectly. An effective batch accumulates assistant token
sums, then divides once by its actual token count (including the final batch).
Checkpoints use safetensors plus JSON; torch.load/pickle is never used.
"""
from pathlib import Path
import math
import time
import importlib.metadata

from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.design_response_schema import PROTOCOL
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_training import (
    AdapterPlan, adapter_state, attach_adapters, file_sha256, frozen_fingerprint,
    load_native, local_target, restore_adapters, supervised_forward,
)
from power_grid_env.study_artifacts import digest, encoded, read_json, seal, verify
from power_grid_env.vlm_batches import VLMExportDataset

MODES = ('image', 'text', 'combined', 'schematic')
SCHEMA = 'power-grid-env.qwen35-mixed-checkpoint/v1'


class MixedPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    rank: int = Field(default=4, ge=1, le=32)
    alpha: float = Field(default=8., gt=0, le=128)
    learning_rate: float = Field(default=0.0002, gt=0, le=0.01)
    gradient_clip: float = Field(default=1., gt=0, le=100)
    threads: int = Field(default=4, ge=1, le=16)
    epochs: int = Field(default=1, ge=1, le=16)
    accumulation: int = Field(default=4, ge=1, le=32)

    def adapter_plan(self):
        return AdapterPlan.model_validate({k: v for k, v in self.model_dump().items()
                                           if k not in ('epochs', 'accumulation')})


class MixedData:
    """All four train exports, identical observation IDs and one public protocol."""
    def __init__(self, root, manifest):
        self.root = Path(root)
        self.manifest = manifest
        if verify(root) != manifest:
            raise ValueError('owner curriculum differs')
        receipt = read_json(self.root/'receipt.json')
        if (receipt.get('schema') != 'power-grid-env.response-schema-collection/v1' or
                receipt.get('passed') is not True or receipt.get('response_protocol') != PROTOCOL or
                receipt.get('test_exported') is not False):
            raise ValueError('train/dev response curriculum required; test stays closed')
        self.datasets = {}
        expected_ids = None
        self.anchors = {}
        for mode in MODES:
            rows = [r for r in receipt['exports'] if r['split'] == 'train' and r['mode'] == mode]
            if len(rows) != 1 or rows[0]['path'] != 'train-'+mode:
                raise ValueError('one train export per modality required')
            row = rows[0]
            data = VLMExportDataset(self.root/row['path'], row['manifest_sha256'])
            header = read_json(data.root/'receipt.json')
            ids = [data.record(i)['metadata']['task_id'] for i in range(len(data))]
            if (data.kind != 'sft' or data.split != 'train' or data.mode != mode or
                    len(data) != row['rows'] or header.get('response_protocol') != PROTOCOL or
                    header['dataset_manifest_sha256'] != receipt['dataset_manifest_sha256'] or
                    (expected_ids is not None and ids != expected_ids)):
                raise ValueError('mixed train split, ordered IDs or protocol differs')
            expected_ids = ids
            self.datasets[mode] = data
            self.anchors[mode] = row['manifest_sha256']
        self.ids = expected_ids
        self.corpus_manifest = receipt['dataset_manifest_sha256']
        self.items = [(mode, i) for i in range(len(expected_ids)) for mode in MODES]

    def schedule(self, plan):
        import torch
        # Dedicated generator: model initialization and sampling cannot perturb order.
        generator = torch.Generator(device='cpu').manual_seed(plan.seed)
        return [self.items[i] for _ in range(plan.epochs)
                for i in torch.randperm(len(self.items), generator=generator).tolist()]

    def check_unchanged(self):
        if verify(self.root) != self.manifest:
            raise ValueError('curriculum changed during training')


def optimizer_state(model, optimizer):
    """Named AdamW moments, never executable optimizer serialization."""
    import torch
    state = {}
    params = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    if len(optimizer.param_groups) != 1 or [id(p) for p in optimizer.param_groups[0]['params']] != [id(p) for _, p in params]:
        raise ValueError('one ordered adapter-only optimizer group required')
    for name, param in params:
        row = optimizer.state[param]
        if set(row) != {'step', 'exp_avg', 'exp_avg_sq'}:
            raise ValueError('complete non-AMSGrad AdamW state required')
        for key, tensor in row.items():
            if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu' or
                    tensor.dtype != torch.float32 or not bool(torch.isfinite(tensor).all()) or
                    (key == 'step' and (tensor.shape != () or float(tensor) < 1 or float(tensor) != int(tensor))) or
                    (key != 'step' and tensor.shape != param.shape) or
                    (key == 'exp_avg_sq' and bool((tensor < 0).any()))):
                raise ValueError('invalid AdamW tensor')
            state[name+'::'+key] = tensor.detach().contiguous().clone()
    return state


def restore_optimizer(model, optimizer, tensors, completed):
    """Validate the entire named state before mutating the optimizer."""
    import torch
    params = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    expected = {n+'::'+k for n, _ in params for k in ('step', 'exp_avg', 'exp_avg_sq')}
    if tensors.keys() != expected:
        raise ValueError('AdamW named state inventory differs')
    prepared = {}
    for name, param in params:
        row = {k: tensors[name+'::'+k] for k in ('step', 'exp_avg', 'exp_avg_sq')}
        for key, value in row.items():
            if (value.dtype != torch.float32 or value.device.type != 'cpu' or
                    not bool(torch.isfinite(value).all()) or
                    (key == 'step' and (value.shape != () or float(value) != completed)) or
                    (key != 'step' and value.shape != param.shape) or
                    (key == 'exp_avg_sq' and bool((value < 0).any()))):
                raise ValueError('AdamW shape/dtype/step/finite check failed')
        prepared[param] = {k: v.clone() for k, v in row.items()}
    optimizer.state.clear()
    optimizer.state.update(prepared)


def update(model, optimizer, examples, clip):
    """Token-weighted accumulation; clip exactly once per optimizer update."""
    import torch
    optimizer.zero_grad(set_to_none=True)
    rows, tokens = [], 0
    for batch, meta in examples:
        loss, logits = supervised_forward(model, batch)
        count = int((batch['labels'] != -100).sum())
        if count != meta['assistant_tokens'] or count <= 0:
            raise ValueError('assistant token denominator differs')
        (loss * count).backward()
        rows.append(dict(**meta, loss=float(loss.detach())))
        tokens += count
        del logits, loss
    if tokens <= 0:
        raise ValueError('empty accumulation group')
    params = [p for p in model.parameters() if p.requires_grad]
    for param in params:
        if param.grad is None or not bool(torch.isfinite(param.grad).all()):
            raise ValueError('missing or nonfinite accumulated gradient')
        param.grad.div_(tokens)
    grad = torch.nn.utils.clip_grad_norm_(params, clip, error_if_nonfinite=True)
    if float(grad) <= 0:
        raise ValueError('zero accumulated adapter gradient')
    optimizer.step()
    if any(not bool(torch.isfinite(p).all()) for p in params):
        raise ValueError('nonfinite updated adapter')
    optimizer.zero_grad(set_to_none=True)
    return dict(examples=rows, assistant_tokens=tokens, gradient_norm=float(grad),
                loss=sum(r['loss']*r['assistant_tokens'] for r in rows)/tokens)


def checkpoint(root, model, optimizer, header, rows, cursor):
    import torch
    from safetensors.torch import save_file
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    save_file(adapter_state(model), str(root/'adapter.safetensors'))
    save_file(optimizer_state(model, optimizer), str(root/'optimizer.safetensors'))
    save_file({'cpu_rng': torch.get_rng_state()}, str(root/'rng.safetensors'))
    report = dict(**header, completed_updates=len(rows), cursor=cursor, rows=rows,
                  adapter_sha256=file_sha256(root/'adapter.safetensors'),
                  optimizer_sha256=file_sha256(root/'optimizer.safetensors'),
                  rng_sha256=file_sha256(root/'rng.safetensors'))
    (root/'receipt.json').write_bytes(encoded(report))
    return seal(root)


def read_checkpoint(root, manifest, header, schedule, plan):
    """Reject different recipes/order/data/code before restoring any state."""
    if verify(root) != manifest:
        raise ValueError('owner resume checkpoint differs')
    report = read_json(Path(root)/'receipt.json')
    if any(report.get(k) != v for k, v in header.items()):
        raise ValueError('resume target, runtime, data, recipe or frozen model differs')
    completed, cursor = report['completed_updates'], report['cursor']
    total = math.ceil(len(schedule)/plan.accumulation)
    if (type(completed) is not int or not 1 <= completed <= total or
            type(cursor) is not int or cursor != min(completed*plan.accumulation, len(schedule)) or
            len(report['rows']) != completed):
        raise ValueError('resume cursor is not an optimizer boundary')
    seen = 0
    for step, row in enumerate(report['rows']):
        group = schedule[step*plan.accumulation:(step+1)*plan.accumulation]
        if (row['step'] != step or len(row['examples']) != len(group) or
                not math.isfinite(row['loss']) or not math.isfinite(row['gradient_norm']) or
                row['gradient_norm'] <= 0):
            raise ValueError('resume update history differs')
        count = 0
        for (mode, index), meta in zip(group, row['examples'], strict=True):
            if (meta['mode'] != mode or meta['index'] != index or
                    meta['task_id'] != header['observation_ids'][index] or meta['split'] != 'train' or
                    meta['kind'] != 'sft' or type(meta['assistant_tokens']) is not int or
                    meta['assistant_tokens'] <= 0 or not math.isfinite(meta['loss'])):
                raise ValueError('resume microbatch history differs')
            count += meta['assistant_tokens']
            seen += 1
        if row['assistant_tokens'] != count:
            raise ValueError('resume effective token denominator differs')
    if seen != cursor:
        raise ValueError('resume history does not cover consumed examples')
    for name in ('adapter', 'optimizer', 'rng'):
        path = Path(root)/(name+'.safetensors')
        if path.stat().st_size > 16_000_000 or file_sha256(path) != report[name+'_sha256']:
            raise ValueError('resume tensor artifact differs')
    return report


def train_mixed(target_path, target_sha256, collection_root, collection_manifest,
                out, plan=None, resume=None, resume_manifest=None, stop_after_updates=None):
    """Complete epoch(s) or deliberately stop on a sealed optimizer boundary.

    A crash during a group replays that group from the last sealed checkpoint.
    stop_after_updates is a total cursor, not a changed learning recipe. Resume
    requires the same producer fingerprint and owner-pinned checkpoint bytes.
    """
    import torch
    from safetensors.torch import load_file
    start = time.monotonic()
    plan = MixedPlan.model_validate(plan or {})
    root = Path(out)
    if root.exists():
        raise ValueError('fresh mixed-training output required')
    if bool(resume) != bool(resume_manifest):
        raise ValueError('resume path and owner manifest must be supplied together')
    producer = package_fingerprint()
    data = MixedData(collection_root, collection_manifest)
    schedule = data.schedule(plan)
    total = math.ceil(len(schedule)/plan.accumulation)
    stop = total if stop_after_updates is None else stop_after_updates
    if type(stop) is not int or not 1 <= stop <= total:
        raise ValueError('stop cursor outside complete recipe')
    target = local_target(target_path, target_sha256)
    torch.manual_seed(plan.seed)
    processor, model = load_native(target, plan.threads)
    modules = attach_adapters(model, plan.adapter_plan())
    frozen = frozen_fingerprint(model)
    header = dict(schema=SCHEMA, code_sha256=producer, model=target['model'],
        target_metadata_sha256=target_sha256, collection_manifest_sha256=collection_manifest,
        corpus_manifest_sha256=data.corpus_manifest, export_manifests=data.anchors,
        response_protocol=PROTOCOL, plan=plan.model_dump(), schedule_sha256=digest(encoded(schedule)),
        scheduled_examples=len(schedule), scheduled_updates=total, observation_ids=data.ids,
        versions={p: importlib.metadata.version(p) for p in ('torch', 'transformers', 'safetensors')},
        frozen_backbone_sha256=frozen, trainable_modules=modules,
        trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        split='train', device='cpu', dtype='float32', normalization='assistant-token-mean',
        optimizer=dict(type='AdamW', betas=[0.9, 0.999], eps=1e-8, weight_decay=0., foreach=False),
        checkpoint_boundary='after-update-zero-grad', deterministic_algorithms=True)
    torch.use_deterministic_algorithms(True)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
        lr=plan.learning_rate, weight_decay=0., foreach=False)
    rows, cursor = [], 0
    if resume:
        report = read_checkpoint(resume, resume_manifest, header, schedule, plan)
        if report['completed_updates'] >= stop:
            raise ValueError('resume must make at least one additional update')
        # Validate optimizer and RNG before mutating model adapters.
        restore_optimizer(model, optimizer, load_file(str(Path(resume)/'optimizer.safetensors')),
                          report['completed_updates'])
        rng = load_file(str(Path(resume)/'rng.safetensors'))
        if (set(rng) != {'cpu_rng'} or rng['cpu_rng'].dtype != torch.uint8 or
                rng['cpu_rng'].shape != torch.get_rng_state().shape):
            raise ValueError('CPU RNG state differs')
        restore_adapters(model, load_file(str(Path(resume)/'adapter.safetensors')))
        torch.set_rng_state(rng['cpu_rng'])
        rows, cursor = report['rows'], report['cursor']
    root.mkdir(parents=True, exist_ok=False)
    (root/'checkpoints').mkdir()
    initial = adapter_state(model)
    last_manifest = None
    for step in range(len(rows), stop):
        group = schedule[cursor:cursor+plan.accumulation]
        def examples():
            for mode, index in group:
                batch, meta = data.datasets[mode].pack(processor, index)
                yield batch, dict(**meta, index=index)
        row = update(model, optimizer, examples(), plan.gradient_clip)
        rows.append(dict(step=step, **row))
        cursor += len(group)
        if package_fingerprint() != producer:
            raise ValueError('runtime changed during training')
        path = root/'checkpoints'/f'update-{step+1:06d}'
        last_manifest = checkpoint(path, model, optimizer, header, rows, cursor)
        print(f'mixed SFT update {step+1}/{total}: examples={cursor}/{len(schedule)} loss={row["loss"]:.6f}', flush=True)
    if (all(torch.equal(v, initial[k]) for k, v in adapter_state(model).items()) or
            frozen_fingerprint(model) != frozen or
            any(p.grad is not None for p in model.parameters() if not p.requires_grad)):
        raise ValueError('adapter did not update or frozen backbone changed')
    data.check_unchanged()
    if local_target(target_path, target_sha256) != target or package_fingerprint() != producer:
        raise ValueError('target or runtime changed during training')
    result = dict(**header, passed=True, complete=cursor == len(schedule),
        completed_updates=len(rows), consumed_examples=cursor,
        last_checkpoint=f'checkpoints/update-{len(rows):06d}', last_checkpoint_manifest=last_manifest,
        resumed_from_manifest=resume_manifest, frozen_backbone_unchanged=True,
        full_model_tuned=False, heldout_evaluated=False, elapsed_s=time.monotonic()-start,
        scope='Train-only mixed views; update-boundary data-only resume. No held-out quality claim.')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


def evaluate_mixed(target_path, target_sha256, collection_root, collection_manifest,
                   corpus_root, corpus_manifest, training, training_manifest, out,
                   plan=None, expected_training_producer=None):
    """Complete dev evaluation, only for a completed mixed training recipe."""
    import torch
    from safetensors.torch import load_file
    from power_grid_env.qwen35_adapter_evaluation import generate_prediction, producer_pin
    from power_grid_env.qwen35_evaluation import GenerationPlan, heldout_exports, summarize
    from power_grid_env.design_multimodal import IndicationGrader
    root = Path(out)
    if root.exists():
        raise ValueError('fresh evaluation output required')
    start = time.monotonic()
    producer = package_fingerprint()
    if verify(training) != training_manifest:
        raise ValueError('owner mixed training differs')
    report = read_json(Path(training)/'receipt.json')
    if (report.get('schema') != SCHEMA or report.get('passed') is not True or
            report.get('complete') is not True or report.get('frozen_backbone_unchanged') is not True or
            report.get('code_sha256') != producer_pin(expected_training_producer) or
            report.get('target_metadata_sha256') != target_sha256 or
            report.get('collection_manifest_sha256') != collection_manifest or
            report.get('corpus_manifest_sha256') != corpus_manifest):
        raise ValueError('completed pinned mixed recipe required for dev evaluation')
    training_plan = MixedPlan.model_validate(report['plan'])
    data = MixedData(collection_root, collection_manifest)
    schedule = data.schedule(training_plan)
    total = math.ceil(len(schedule)/training_plan.accumulation)
    if (report['last_checkpoint'] != f'checkpoints/update-{total:06d}' or
            report['completed_updates'] != total or report['consumed_examples'] != len(schedule)):
        raise ValueError('complete checkpoint cursor differs')
    target = local_target(target_path, target_sha256)
    if target['model'] != report['model']:
        raise ValueError('training and evaluation models differ')
    generation = GenerationPlan.model_validate(plan or {})
    datasets = heldout_exports(collection_root, collection_manifest, corpus_root, corpus_manifest, 'dev')
    grader = IndicationGrader(corpus_root, corpus_manifest)
    torch.manual_seed(training_plan.seed)
    processor, model = load_native(target, generation.threads)
    modules = attach_adapters(model, training_plan.adapter_plan())
    frozen = frozen_fingerprint(model)
    if (frozen != report['frozen_backbone_sha256'] or modules != report['trainable_modules'] or
            sum(p.numel() for p in model.parameters() if p.requires_grad) != report['trainable_parameters'] or
            report['export_manifests'] != data.anchors or report['response_protocol'] != PROTOCOL or
            report['schedule_sha256'] != digest(encoded(schedule))):
        raise ValueError('mixed checkpoint model or train schedule differs')
    checkpoint_root = Path(training)/report['last_checkpoint']
    # The training receipt extends the immutable checkpoint header. Checking its
    # original fields also validates every consumed microbatch and token count.
    header_keys = ('schema', 'code_sha256', 'model', 'target_metadata_sha256',
        'collection_manifest_sha256', 'corpus_manifest_sha256', 'export_manifests',
        'response_protocol', 'plan', 'schedule_sha256', 'scheduled_examples',
        'scheduled_updates', 'observation_ids', 'versions', 'frozen_backbone_sha256', 'trainable_modules',
        'trainable_parameters', 'split', 'device', 'dtype', 'normalization',
        'optimizer', 'checkpoint_boundary', 'deterministic_algorithms')
    ck = read_checkpoint(checkpoint_root, report['last_checkpoint_manifest'],
                         {k: report[k] for k in header_keys}, schedule, training_plan)
    restore_adapters(model, load_file(str(checkpoint_root/'adapter.safetensors')))
    before = adapter_state(model)
    rows = []
    root.mkdir(parents=True, exist_ok=False)
    with (root/'predictions.jsonl').open('wb') as stream:
        for dataset in datasets:
            for index in range(len(dataset)):
                row = generate_prediction(model, processor, dataset, index, generation, grader)
                rows.append(row)
                stream.write(encoded(row)+b'\n')
                stream.flush()
                print('mixed dev', dataset.mode, index+1, '/', len(dataset), row['reward'], flush=True)
    if (frozen_fingerprint(model) != frozen or
            any(not torch.equal(before[k], v) for k, v in adapter_state(model).items()) or
            verify(training) != training_manifest or verify(corpus_root) != corpus_manifest or
            local_target(target_path, target_sha256) != target or package_fingerprint() != producer):
        raise ValueError('mixed evaluation inputs or parameters changed')
    data.check_unchanged()
    result = dict(schema='power-grid-env.qwen35-adapter-evaluation/v1', passed=True,
        code_sha256=producer, model=target['model'], target_metadata_sha256=target_sha256,
        collection_manifest_sha256=collection_manifest, corpus_manifest_sha256=corpus_manifest,
        training_manifest_sha256=training_manifest, training_code_sha256=report['code_sha256'],
        explicit_training_revision_pin=expected_training_producer is not None,
        training_kind='mixed-view-token-normalized-sft', response_protocol=PROTOCOL,
        adapter_sha256=ck['adapter_sha256'], split='dev', plan=generation.model_dump(),
        metrics=summarize(rows, len(datasets[0])), predictions=len(rows),
        device='cpu', dtype='float32', decoding='greedy', labels_passed_to_model=False,
        adapters_loaded=True, model_parameters_unchanged=True, elapsed_s=time.monotonic()-start,
        model_improvement_claimed=False,
        scope='Complete schema-protocol dev diagnosis; no test, repair or engineering qualification.')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result

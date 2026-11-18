"""Controlled depth2 -> depth8 LoRA arm; never continues the reference run.

``plan`` uses read-only native forwards, one model at a time, and no optimizer.
``run`` requires that sealed plan and repeats its initialization proof before
creating fresh AdamW. Both commands require a sealed, replay-audited reference.
The original phase, checkpoints, observations, and primary scores are immutable.
"""
import argparse
import copy
import gc
import hashlib
import json
import math
from pathlib import Path

from benchmarks import engineering_physics_campaign as physics
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_native_staged_policy import NativePolicy, checkpoint, load
from benchmarks.engineering_skill_tasks import identity, score
from benchmarks.engineering_staged_audit import replay
from benchmarks.engineering_trainer_v2 import LoraSpec, attach_language_lora
from benchmarks.engineering_value_loss import objective
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, seal, verify


VARIANT = 'last8_capacity'
CANDIDATE = 'last8_capacity78'
REFERENCE_PLAN = 'reports/engineering-physics-joint-plan-2026-11-18'
REFERENCE_RESULT = 'reports/engineering-physics-joint-phase-2026-11-18'
REFERENCE_AUDIT = 'reports/engineering-physics-joint-replay-2026-11-18'
REFERENCE_RESULT_SHA = '146bbff245df9260d37fd1814de73f143bfda4592e19f00c0d6ee78672ddb25b'
REFERENCE_AUDIT_SHA = 'fe22968bb25214eda5ca1a29e0c4ed649aca83fc67a8df24f6b28c40097f2fe8'
FILES = tuple(sorted(set(physics.FILES) | {
    'engineering_capacity_campaign.py', 'engineering_staged_audit.py',
    'engineering_replacement_challenges.py', 'engineering_catalog_challenges.py',
    'engineering_card_curriculum.py', 'engineering_scenarios.py',
    'engineering_skill_tasks.py', 'engineering_reference.py',
    'engineering_operation_probe.py', 'engineering_train.py',
    'editor_steady.py', 'engineering_action_menu.py', 'engineering_adapter.py',
    'engineering_agent.py', 'engineering_choice_episode.py', 'engineering_factory.py',
    'engineering_learning_gate.py', 'engineering_observations.py',
    'engineering_protocol.py', 'engineering_public_policy.py', 'engineering_qwen_policy.py',
    'engineering_recovery.py', 'engineering_rollout.py', 'engineering_sft.py',
    'engineering_skill_pilot.py', 'export_grid_studio.py', 'grid_editor.py',
    'engineering_causal_audit.py',
}))
ALIGNMENT_SCHEMA = 'power-grid-env.native-causal-audit/v1'
ALIGNMENT_AUDIT = 'reports/engineering-native-causal-audit-2026-11-18'
ALIGNMENT_CHECKS = ('teacher_vs_prefix', 'cache_prefill', 'train_vs_eval')
# The frozen physics profile's overloaded operation, simulate operation, and
# first conditional arguments, in selection order. Positions are the union of
# first target, first payload and last payload in that receipt (3+2+3 = 8).
# This is deliberately NOT an all15 causal audit or a parent40 audit.
ALIGNMENT_COVERAGE = ((0, 'operation', (0, 3, 5)), (3, 'operation', (0, 3)),
                      (4, 'conditional_arguments', (0, 4, 13)))
ALIGNMENT_SCOPE = ('Reference physics78 depth2 checkpoint only: 3 selected train states, '
                   '8 target positions and text-image-text generation invariance. '
                   'Not all15 inputs, parent40, depth8 or all-runtime qualification. '
                   'The separate parent40/depth8 initialization proof covers all15.')
SCOPE = ('Controlled train calibration: only LoRA last_blocks changes from 2 to 8; '
         'same parent40, 78 updates and public staged v2 workflows. No heldout, '
         'generalization, industrial or resource superiority claim.')


def source_inventory():
    return {f: identity(Path(__file__).with_name(f)) for f in FILES}


def fresh_output(out, reference, p, extra=()):
    root = Path(out)
    protected = [v['path'] for v in reference.values()]
    protected += [v['path'] for v in p['sources'].values()]
    protected += [p['initial']['checkpoint'], p['initial']['plan'], *extra]
    if root.exists() or any(root.resolve().is_relative_to(Path(x).resolve()) for x in protected):
        raise ValueError('fresh output outside all sealed inputs required')
    return root


def default_reference():
    return dict(plan=dict(path=REFERENCE_PLAN, manifest_sha256=verify(REFERENCE_PLAN)),
                result=dict(path=REFERENCE_RESULT, manifest_sha256=REFERENCE_RESULT_SHA),
                audit=dict(path=REFERENCE_AUDIT, manifest_sha256=REFERENCE_AUDIT_SHA))


def capacity_recipe(reference):
    """Pure deterministic recipe transform. Reference validation precedes this.

    Preserve every field (including admission, examples, parent and schedule);
    add campaign metadata separately. Reject attempts to expand a different arm.
    """
    expected = dict(schema='power-grid-env.physics-curriculum-plan/v1',
                    target_sha256=physics.TARGET, steps=78, seed=423,
                    learning_rate=.0001, gradient_clip=1., value_weight=4.,
                    context_tokens=8192, completion_tokens=128,
                    maximum_tool_calls_per_episode=24, thinking=False,
                    optimizer_run=False, lora=LoraSpec().model_dump(),
                    optimizer_initialization='fresh_AdamW_for_new_phase_preserve_adapter40_weights',
                    gate='all15_generated_answers_and_all3_full_v2_workflows')
    if 'variant' in reference or any(digest(reference.get(k)) != digest(v) for k, v in expected.items()):
        raise ValueError('reference must be the original fixed depth2 physics recipe')
    indices = reference['indices']; transfer = reference['transfer_indices']
    if (len(indices) != 13 or len(transfer) != 2 or len(set(indices + transfer)) != 15
            or any(type(i) is not int or i < 0 for i in indices + transfer)
            or reference['schedule'] != physics.order(indices)
            or set(reference['row_ids']) != {str(i) for i in indices + transfer}
            or reference['native_admission']['passed'] is not True
            or reference['native_admission']['checked_targets'] != 15
            or [r['index'] for r in reference['native_admission']['rows']] != indices + transfer):
        raise ValueError('reference selection/admission/schedule differs')
    result = copy.deepcopy(reference)
    result['lora']['last_blocks'] = 8
    return result


def _keys(names):
    if (not isinstance(names, list) or not names or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not n.startswith('model.language_model.layers.')
                   or '.base.' in n for n in names)):
        raise ValueError('distinct native adapter module names required')
    return {name + suffix for name in names for suffix in ('.a', '.b')}


def merge_parent(initialized, parent, parent_names, expanded_names):
    """Return a complete state, retaining every parent tensor and fresh extra A.

    No partial restore: inventories, shapes, dtype, finite values, A/B pairing,
    and zero extra B are checked before constructing the returned state.
    Inputs are never mutated and returned tensors do not alias them.
    """
    import torch
    old_keys = _keys(parent_names); new_keys = _keys(expanded_names)
    if (set(parent) != old_keys or set(initialized) != new_keys
            or not set(parent_names) < set(expanded_names)):
        raise ValueError('complete parent and strictly expanded adapter inventories required')
    for name in expanded_names:
        a, b = initialized[name + '.a'], initialized[name + '.b']
        if (not isinstance(a, torch.Tensor) or not isinstance(b, torch.Tensor)
                or a.ndim != 2 or b.ndim != 2 or a.shape[0] != b.shape[1]
                or not a.is_floating_point() or a.dtype != b.dtype
                or not bool(torch.isfinite(a).all()) or not bool(torch.isfinite(b).all())):
            raise ValueError('finite compatible initialized A/B required')
        if name not in parent_names and bool(torch.count_nonzero(b)):
            raise ValueError('extra B must be zero for function preservation')
    for key, tensor in parent.items():
        target = initialized[key]
        if (not isinstance(tensor, torch.Tensor) or tensor.shape != target.shape
                or tensor.dtype != target.dtype or not bool(torch.isfinite(tensor).all())):
            raise ValueError('parent adapter shape/dtype/finite mismatch: ' + key)
    return {key: (parent[key] if key in parent else value).detach().cpu().contiguous().clone()
            for key, value in initialized.items()}


def attach_fresh(model, spec):
    # attach_language_lora itself can find projections inside existing wrappers.
    # This arm always loads a fresh base and refuses such nested expansion.
    if (any(p.requires_grad for p in model.parameters())
            or any(all(hasattr(m, a) for a in ('base', 'a', 'b'))
                   for m in model.modules())):
        raise ValueError('fresh frozen unwrapped base required')
    return attach_language_lora(model, spec)


def tensor_sha256(tensor):
    import torch
    value = tensor.detach().cpu().contiguous()
    h = hashlib.sha256()
    h.update(json.dumps(dict(dtype=str(value.dtype), shape=list(value.shape)),
                        sort_keys=True, separators=(',', ':')).encode())
    h.update(value.reshape(-1).view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def state_sha256(state):
    return digest({key: tensor_sha256(value) for key, value in sorted(state.items())})


def signatures(actor, data, p):
    """SHA256 exact selected causal logits, ordinary CE and weighted loss on 15.

    These are teacher-forced forward checks, not generated primary scores.
    Only tensor hashes are kept, so logits and models need not coexist in memory.
    """
    import torch
    from power_grid_env.qwen35_training import supervised_forward
    actor.model.eval(); rows = []
    for i in p['indices'] + p['transfer_indices']:
        batch, mask, meta = data.batch(actor.processor, i)
        if dict(index=i, **meta) != next(r for r in p['native_admission']['rows'] if r['index'] == i):
            raise ValueError('capacity native admission differs')
        with torch.inference_mode():
            ordinary, logits = supervised_forward(actor.model, batch)
            weighted, metrics = objective(logits, batch['labels'][batch['labels'] != -100],
                                          mask, p['value_weight'])
        if abs(float(ordinary) - metrics['mean_ce']) > 1e-5:
            raise ValueError('capacity causal loss alignment differs')
        rows.append(dict(index=i, row_id=meta['row_id'],
                         input_sha256=state_sha256(batch),
                         logits_sha256=tensor_sha256(logits),
                         loss_sha256=tensor_sha256(ordinary),
                         weighted_loss_sha256=tensor_sha256(weighted)))
        del batch, ordinary, logits, weighted
    return rows


def _check_skills(values, data, p, candidate):
    if [x['index'] for x in values] != p['indices'] + p['transfer_indices']:
        raise ValueError('skill evaluation inventory differs')
    for row in values:
        _, answer = data.messages(row['index'])
        if (digest(row['expected']) != digest(answer) or row['candidate'] != candidate
                or row['skill'] != data.rows[row['index']]['skill']
                or row['group'] != ('train' if row['index'] in p['indices'] else 'untrained_question')
                or score(row['completion'], answer) != {k: row[k] for k in ('format_valid', 'exact')}):
            raise ValueError('generated skill score does not replay')
    return sum(x['exact'] for x in values)


def score_result(before, after, before_workflows, after_workflows):
    if len(before) != 15 or len(after) != 15 or len(before_workflows) != 3 or len(after_workflows) != 3:
        raise ValueError('complete 15+3 primary denominators required')
    return dict(before_skills=sum(r['exact'] for r in before),
                after_skills=sum(r['exact'] for r in after), skill_targets=15,
                before_workflows=sum(r['success'] for r in before_workflows),
                after_workflows=sum(r['success'] for r in after_workflows), workflow_tasks=3)


def reference_evidence(reference, processor=None):
    """Read-only validation of all six traces and both 15-answer inventories.

    A failed learning gate is permitted; missing sealing/replay evidence is not.
    No reference output or posthoc probe is ever rewritten or used as a target.
    """
    if set(reference) != {'plan', 'result', 'audit'}:
        raise ValueError('reference plan/result/audit anchors required')
    for item in reference.values():
        if set(item) != {'path', 'manifest_sha256'} or verify(item['path']) != item['manifest_sha256']:
            raise ValueError('sealed reference manifest differs')
    rp, rr, ra = (reference[k] for k in ('plan', 'result', 'audit'))
    data, p = physics.load_plan(rp['path'], rp['manifest_sha256'])
    capacity_recipe(p)
    r, _ = checkpoint(rr['path'], rr['manifest_sha256'], rp['path'], rp['manifest_sha256'], physics.TARGET)
    if r.get('variant') is not None or r['initial_manifest_sha256'] != p['initial']['manifest_sha256']:
        raise ValueError('original depth2 result and parent40 required')
    audit = read_json(Path(ra['path']) / 'summary.json')
    if (audit.get('schema') != 'power-grid-env.staged-campaign-audit/v1'
            or audit.get('replay_passed') is not True
            or audit.get('native_prompt_budgets_checked') is not True
            or audit['source_manifest_sha256'] != rr['manifest_sha256']
            or audit['plan_manifest_sha256'] != rp['manifest_sha256']):
        raise ValueError('reference must be fully replay-audited')
    rows = []; traces = {}
    for candidate, key in (('adapter40', 'before'), ('physics78', 'after')):
        _check_skills(r[key], data, p, candidate)
        traces[candidate] = []
        for case in data.cases(p['case_ids']):
            trace = read_json(Path(rr['path']) / 'episodes' / (candidate + '-' + case['id'] + '.json'))
            if trace['candidate'] != candidate:
                raise ValueError('reference candidate attribution differs')
            rows.append(dict(candidate=candidate, **replay(case, trace, processor)))
            traces[candidate].append(trace)
        compact = [dict(task_id=t['task_id'], success=t['success'], calls=t['calls'])
                   for t in traces[candidate]]
        if r['workflow_results'][candidate] != compact:
            raise ValueError('reference workflow summary differs')
    expected = score_result(r['before'], r['after'], traces['adapter40'], traces['physics78'])
    gate = expected['after_skills'] == 15 and expected['after_workflows'] == 3
    if (audit['rows'] != rows or audit['score'] != expected or r['score'] != expected
            or audit['gate_passed'] != gate or r['gate_passed'] != gate):
        raise ValueError('reference replay/primary scores differ')
    before = read_json(Path(rr['path']) / 'before.json')
    if before != dict(complete=True, skills=r['before'], workflow_successes=expected['before_workflows']):
        raise ValueError('reference baseline file differs')
    return data, p, r, traces['adapter40']


def _expanded(target, p):
    """Fresh base + one depth8 attachment + full guarded parent40 merge."""
    import torch
    from safetensors.torch import load_file
    from power_grid_env.qwen35_training import local_target, load_native, adapter_state, restore_adapters
    parent = p['initial']
    r, _ = checkpoint(parent['checkpoint'], parent['manifest_sha256'], parent['plan'],
                      parent['plan_manifest_sha256'], p['target_sha256'])
    processor, model = load_native(local_target(target, p['target_sha256']), 4)
    torch.manual_seed(p['seed'])
    names = attach_fresh(model, LoraSpec.model_validate(p['lora']))
    old = load_file(str(Path(parent['checkpoint']) / 'adapter.safetensors'))
    merged = merge_parent(adapter_state(model), old, r['adapter_names'], names)
    restore_adapters(model, merged)
    metadata = dict(parent_adapter_names=r['adapter_names'], expanded_adapter_names=names,
                    parent_adapter_state_sha256=state_sha256(old),
                    expanded_adapter_state_sha256=state_sha256(merged),
                    extra_b_zero=True, parent_tensors_retained=True)
    return NativePolicy(processor, model), metadata


def _parent_signatures(target, data, p):
    parent = p['initial']
    actor = load(parent['checkpoint'], parent['manifest_sha256'], parent['plan'],
                 parent['plan_manifest_sha256'], target, p['target_sha256'])
    try:
        return signatures(actor, data, p)
    finally:
        del actor
        gc.collect()


def _expanded_signatures(target, data, p):
    actor, metadata = _expanded(target, p)
    try:
        return signatures(actor, data, p), metadata
    finally:
        del actor
        gc.collect()


def prove_initialization(target, data, reference):
    original = _parent_signatures(target, data, reference)
    expanded, metadata = _expanded_signatures(target, data, capacity_recipe(reference))
    if original != expanded:
        raise ValueError('expanded initialization is not bit-exact on all 15 causal inputs')
    return dict(schema='power-grid-env.capacity-initialization-proof/v1', checked_inputs=15,
                exact_equal=True, parent40=original, expanded=expanded, **metadata,
                scope='Zero extra B plus retained parent tensors; exact SHA256 selected causal '
                      'logits/CE/weighted loss on 15 inputs, not generated score evidence.')


def validate_proof(proof, p):
    if (proof.get('schema') != 'power-grid-env.capacity-initialization-proof/v1'
            or proof.get('checked_inputs') != 15 or proof.get('exact_equal') is not True
            or proof.get('extra_b_zero') is not True or proof.get('parent_tensors_retained') is not True
            or proof['parent40'] != proof['expanded']
            or [r['index'] for r in proof['parent40']] != p['indices'] + p['transfer_indices']
            or not set(proof['parent_adapter_names']) < set(proof['expanded_adapter_names'])):
        raise ValueError('complete exact parent40/expanded proof required')
    _keys(proof['parent_adapter_names']); _keys(proof['expanded_adapter_names'])
    def sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    for row in proof['parent40']:
        if (row['row_id'] != p['row_ids'][str(row['index'])]
                or set(row) != {'index', 'row_id', 'input_sha256', 'logits_sha256',
                                'loss_sha256', 'weighted_loss_sha256'}
                or any(not sha(row[k]) for k in row if k.endswith('_sha256'))):
            raise ValueError('proof input identity/hash differs')
    if not all(sha(proof[k]) for k in ('parent_adapter_state_sha256', 'expanded_adapter_state_sha256')):
        raise ValueError('proof adapter state hashes required')


def baseline_lineage(reference, p, proof):
    root = Path(reference['result']['path'])
    files = ['before.json'] + ['episodes/adapter40-' + task + '.json' for task in p['full_episode_ids']]
    return dict(reused=True, reference=copy.deepcopy(reference),
                files={f: identity(root / f) for f in files},
                initialization_proof_sha256=digest(proof),
                scope='Sealed reference parent40 baseline reused verbatim; not regenerated '
                      'by depth8. Function-preserving construction and 15-input proof recorded.')


def make_plan(reference_plan, reference, proof):
    result = capacity_recipe(reference_plan)
    validate_proof(proof, result)
    result.update(variant=VARIANT, reference=copy.deepcopy(reference),
                  initialization_proof=copy.deepcopy(proof), source_sha256=source_inventory(),
                  baseline_reused=baseline_lineage(reference, result, proof), capacity_scope=SCOPE,
                  training_prerequisites=dict(alignment_schema=ALIGNMENT_SCHEMA,
                                              required_passed_checks=list(ALIGNMENT_CHECKS),
                                              alignment_scope=ALIGNMENT_SCOPE))
    return result


def plan(target, out, reference=None):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh capacity plan output required')
    reference = copy.deepcopy(reference if reference is not None else default_reference())
    data, original, _, _ = reference_evidence(reference)
    fresh_output(out, reference, original)
    inventory = source_inventory(); core = package_fingerprint()
    proof = prove_initialization(target, data, original)
    # Revalidate all pinned inputs after the read-only forwards, before sealing.
    reference_evidence(reference)
    if source_inventory() != inventory or package_fingerprint() != core:
        raise ValueError('capacity source/core changed during planning')
    result = make_plan(original, reference, proof)
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / 'summary.json', result); seal(root)
    return result


def load_plan(root, anchor):
    if verify(root) != anchor:
        raise ValueError('owner capacity plan differs')
    p = read_json(Path(root) / 'summary.json')
    if p.get('variant') != VARIANT or p.get('source_sha256') != source_inventory():
        raise ValueError('capacity variant/source inventory differs')
    data, original, result, baseline = reference_evidence(p['reference'])
    expected = make_plan(original, p['reference'], p['initialization_proof'])
    if digest(p) != digest(expected):
        raise ValueError('only depth2 -> depth8 recipe transformation is permitted')
    parent = read_json(Path(p['initial']['checkpoint']) / 'summary.json')
    if p['initialization_proof']['parent_adapter_names'] != parent['adapter_names']:
        raise ValueError('initialization proof parent attribution differs')
    return data, p, result, baseline


def control_comparison(control, capacity):
    return dict(changed_hyperparameter='lora.last_blocks', reference_last_blocks=2,
                capacity_last_blocks=8, reference_score=copy.deepcopy(control),
                capacity_score=copy.deepcopy(capacity),
                after_skill_delta=capacity['after_skills'] - control['after_skills'],
                after_workflow_delta=capacity['after_workflows'] - control['after_workflows'],
                scope=SCOPE)


def alignment_evidence(item, p):
    """Required run-only prerequisite; no primary-score or recipe modification.

    Accept the frozen engineering_causal_audit.py receipt as produced. It
    attributes the three-state audit to REFERENCE physics78, not parent40.
    Verify all 8 expected target positions, every comparison, the reported max,
    and actual text-image-text invariance; never extrapolate it to all15.
    """
    if (not isinstance(item, dict) or set(item) != {'path', 'manifest_sha256'}
            or verify(item['path']) != item['manifest_sha256']):
        raise ValueError('sealed passing causal/cache alignment audit required before training')
    audit = read_json(Path(item['path']) / 'summary.json')
    expected = dict(schema=ALIGNMENT_SCHEMA, passed=True, optimizer_run=False,
                    frozen_base_unchanged=True,
                    plan_manifest_sha256=p['reference']['plan']['manifest_sha256'],
                    result_manifest_sha256=p['reference']['result']['manifest_sha256'],
                    source_sha256=identity(Path(__file__).with_name('engineering_causal_audit.py')))
    if any(digest(audit.get(k)) != digest(v) for k, v in expected.items()):
        raise ValueError('causal/cache alignment attribution/source differs')
    coverage = [(p['indices'][slot], skill, position)
                for slot, skill, positions in ALIGNMENT_COVERAGE for position in positions]
    rows = audit.get('rows')
    if (not isinstance(rows, list) or not rows or
            [(r.get('index'), r.get('skill'), r.get('target_position')) for r in rows] != coverage):
        raise ValueError('causal/cache alignment requires exact 3-state/8-position coverage')
    def difference(check):
        value = check.get('maximum_absolute_difference')
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= .001
                or check.get('top_token_equal') is not True):
            raise ValueError('causal/cache alignment numerical/top-token check failed')
        return value
    errors = [difference(row[k]) for row in rows for k in ALIGNMENT_CHECKS]
    if (type(audit.get('maximum_logit_difference')) not in (int, float)
            or audit['maximum_logit_difference'] != max(errors)):
        raise ValueError('causal/cache alignment reported maximum differs')
    state = audit['text_image_text']
    difference(state['first_step_logits'])
    if state.get('generated_ids_equal') is not True:
        raise ValueError('causal/cache alignment text-image-text generation differs')
    return copy.deepcopy(item)


def train(plan_root, anchor, target, out, alignment_audit=None):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh capacity run output required')
    data, p, reference, before_workflows = load_plan(plan_root, anchor)
    alignment = alignment_evidence(alignment_audit, p)
    fresh_output(out, p['reference'], p, extra=(plan_root, alignment['path']))
    from power_grid_env.qwen35_training import (
        supervised_forward, adapter_state, restore_adapters, frozen_fingerprint)
    from benchmarks.engineering_train import save_optimizer
    from safetensors.torch import save_file, load_file
    import torch

    core = package_fingerprint(); inventory = source_inventory()
    actor, metadata = _expanded(target, p)
    model, processor = actor.model, actor.processor
    proof = p['initialization_proof']
    frozen = frozen_fingerprint(model)
    for key, value in metadata.items():
        if proof[key] != value:
            raise ValueError('capacity initialized state differs from sealed proof')
    if signatures(actor, data, p) != proof['expanded'] or frozen_fingerprint(model) != frozen:
        raise ValueError('capacity pre-update function/frozen proof differs')
    # Reuse only certified parent40 baseline; no reference after-score selection.
    reference_evidence(p['reference'], processor)
    before = copy.deepcopy(reference['before'])
    root.mkdir(parents=True, exist_ok=False)
    source = Path(p['reference']['result']['path'])
    for name, sha in p['baseline_reused']['files'].items():
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('baseline file changed')
        destination = root / name; destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(raw)
    write_json(root / 'state.json', dict(phase='initialization_verified', complete=False,
                                       initialization_proof_sha256=digest(proof)))
    initial_state = adapter_state(model)
    torch.manual_seed(p['seed'])
    params = [v for v in model.parameters() if v.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=p['learning_rate'], weight_decay=0.)
    history = []; visual = [0]
    def count(*args):
        visual[0] += 1
    hook = model.model.visual.register_forward_hook(count)
    try:
        for step, i in enumerate(p['schedule'], 1):
            model.train(); batch, mask, meta = data.batch(processor, i)
            optimizer.zero_grad(set_to_none=True)
            ordinary, logits = supervised_forward(model, batch)
            labels = batch['labels'][batch['labels'] != -100]
            loss, metrics = objective(logits, labels, mask, p['value_weight'])
            if abs(float(ordinary.detach()) - metrics['mean_ce']) > 1e-5:
                raise ValueError('capacity causal loss alignment differs')
            loss.backward()
            if any(v.grad is None or not bool(torch.isfinite(v.grad).all()) for v in params):
                raise ValueError('nonfinite/missing capacity gradients')
            grad = torch.nn.utils.clip_grad_norm_(params, p['gradient_clip'], error_if_nonfinite=True)
            if float(grad) <= 0:
                raise ValueError('zero capacity gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(v).all()) for v in params):
                raise ValueError('nonfinite capacity weights')
            row = dict(step=step, **meta, metrics=metrics, weighted_loss=float(loss.detach()),
                       gradient_norm=float(grad))
            history.append(row)
            write_json(root / 'state.json', dict(phase='training', complete=False,
                                               completed_steps=step, total_steps=78))
            print(json.dumps(row), flush=True)
            del loss, ordinary, logits
        model.eval()
        if frozen_fingerprint(model) != frozen:
            raise ValueError('capacity phase changed frozen base/vision')
        state = adapter_state(model)
        if all(torch.equal(v, initial_state[k]) for k, v in state.items()):
            raise ValueError('capacity phase made no update')
        save_file(state, str(root / 'adapter.safetensors'))
        optimizer_names = save_optimizer(model, optimizer, root / 'optimizer.safetensors')
        with torch.inference_mode():
            expected_loss, expected_logits = supervised_forward(model, batch)
        restore_adapters(model, {k: torch.zeros_like(v) for k, v in state.items()})
        restore_adapters(model, load_file(str(root / 'adapter.safetensors')))
        with torch.inference_mode():
            actual_loss, actual_logits = supervised_forward(model, batch)
        if not torch.equal(expected_loss, actual_loss) or not torch.equal(expected_logits, actual_logits):
            raise ValueError('capacity adapter roundtrip differs')
        del expected_loss, expected_logits, actual_loss, actual_logits, state, initial_state
        write_json(root / 'state.json', dict(phase='after_evaluation', complete=False, completed_steps=78))
        after = physics.skills(actor, data, p, CANDIDATE)
        after_workflows = physics.workflows(actor, data, p, CANDIDATE, root / 'episodes')
        _check_skills(after, data, p, CANDIDATE)
        replay_rows = [replay(case, trace, processor) for case, trace in
                       zip(data.cases(p['case_ids']), after_workflows)]
    finally:
        hook.remove()
    if (visual[0] < 18 or frozen_fingerprint(model) != frozen or package_fingerprint() != core
            or source_inventory() != inventory):
        raise ValueError('capacity vision/frozen/core/source qualification failed')
    # Pin the reference again before publishing a complete result.
    reference_evidence(p['reference'])
    alignment_evidence(alignment, p)
    scores = score_result(before, after, before_workflows, after_workflows)
    gate = scores['after_skills'] == 15 and scores['after_workflows'] == 3
    compact = lambda rows: [dict(task_id=r['task_id'], success=r['success'], calls=r['calls']) for r in rows]
    result = dict(schema='power-grid-env.physics-curriculum-result/v1', variant=VARIANT,
                  complete=True, steps=78, plan_manifest_sha256=anchor, core_sha256=core,
                  initial_manifest_sha256=p['initial']['manifest_sha256'], reference=p['reference'],
                  lora=p['lora'], adapter_names=metadata['expanded_adapter_names'],
                  optimizer_parameter_names=optimizer_names, source_sha256=inventory,
                  frozen_base_unchanged=True, frozen_base_sha256=frozen, adapter_roundtrip_exact=True,
                  initialization_rechecked=True, initialization_proof_sha256=digest(proof),
                  alignment_audit=alignment,
                  alignment_audit_scope=ALIGNMENT_SCOPE,
                  baseline_reused=p['baseline_reused'], visual_encoder_forward_calls=visual[0],
                  history=history, before=before, after=after,
                  workflow_results={'adapter40': compact(before_workflows), CANDIDATE: compact(after_workflows)},
                  after_workflow_replay=replay_rows, score=scores, gate_passed=gate,
                  control_comparison=control_comparison(reference['score'], scores),
                  full_heldout_or_industrial_claim=False, scope=SCOPE)
    write_json(root / 'summary.json', result)
    write_json(root / 'state.json', dict(phase='complete', complete=True, gate_passed=gate))
    seal(root)
    return {k: v for k, v in result.items() if k not in ('before', 'after', 'history')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'run'])
    parser.add_argument('--target', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--plan'); parser.add_argument('--plan-manifest-sha256')
    parser.add_argument('--reference-plan', default=REFERENCE_PLAN)
    parser.add_argument('--reference-plan-manifest-sha256')
    parser.add_argument('--reference-result', default=REFERENCE_RESULT)
    parser.add_argument('--reference-result-manifest-sha256', default=REFERENCE_RESULT_SHA)
    parser.add_argument('--reference-audit', default=REFERENCE_AUDIT)
    parser.add_argument('--reference-audit-manifest-sha256', default=REFERENCE_AUDIT_SHA)
    parser.add_argument('--alignment-audit', default=ALIGNMENT_AUDIT)
    parser.add_argument('--alignment-audit-manifest-sha256')
    args = parser.parse_args()
    if args.command == 'plan':
        reference = {
            role: dict(path=getattr(args, 'reference_' + role), manifest_sha256=
                       getattr(args, 'reference_' + role + '_manifest_sha256') or
                       verify(getattr(args, 'reference_' + role)))
            for role in ('plan', 'result', 'audit')}
        result = plan(args.target, args.out, reference)
    else:
        if not args.plan or not args.plan_manifest_sha256:
            parser.error('run requires --plan and --plan-manifest-sha256')
        if not args.alignment_audit or not args.alignment_audit_manifest_sha256:
            parser.error('run requires a sealed passing --alignment-audit and its --alignment-audit-manifest-sha256')
        result = train(args.plan, args.plan_manifest_sha256, args.target, args.out,
                       dict(path=args.alignment_audit,
                            manifest_sha256=args.alignment_audit_manifest_sha256))
    print(json.dumps(result))


if __name__ == '__main__':
    main()

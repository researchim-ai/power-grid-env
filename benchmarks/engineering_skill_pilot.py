"""Preregistered finite eight-exemplar skill experiment, fresh local Qwen base.

All generated answers are scored, including malformed output. A new menu order
is a presentation probe, not an independent network. No automatic extra epochs.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from benchmarks.engineering_skill_tasks import SkillDataset, identity, score
from benchmarks.engineering_trainer_v2 import LoraSpec, attach_language_lora
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify, read_json, seal

DEPENDENCIES = ('engineering_skill_tasks.py', 'engineering_skill_pilot.py',
                'engineering_action_menu.py', 'engineering_card_curriculum.py',
                'engineering_grounding_runtime.py', 'engineering_grounding.py',
                'engineering_trainer_v2.py')


def sources():
    names = dict(card='engineering-card-curriculum-release', choice='engineering-action-choice-curriculum',
                 catalog='engineering-catalog-selection-final', visual='engineering-visual-grounding-release')
    return {k: dict(path='reports/' + v + '-2026-11-18', manifest_sha256=verify(Path('reports') / (v + '-2026-11-18'))) for k, v in names.items()}


def create_plan(source_spec, target_sha, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh skill plan required')
    data = SkillDataset(source_spec)
    indices = data.pilot_indices()
    schedule = []
    rng = random.Random(420)
    for repeat in range(4):
        order = list(indices)
        rng.shuffle(order)
        schedule.extend(dict(index=i, row_id=data.rows[i]['id'], presentation_seed=420 + repeat) for i in order)
    result = dict(schema='power-grid-env.skill-pilot-plan/v1', sources=source_spec, target_sha256=target_sha,
                  seed=420, steps=32, schedule=schedule,
                  exemplars=[dict(index=i, row_id=data.rows[i]['id'], skill=data.rows[i]['skill']) for i in indices],
                  source_rows=len(data.rows), exact_duplicate_rows_removed=data.duplicate_rows,
                  available_skills=dict(Counter(r['skill'] for r in data.rows)),
                  source_sha256={f: identity(Path(__file__).with_name(f)) for f in DEPENDENCIES},
                  lora=LoraSpec().model_dump(), learning_rate=.0001, gradient_clip=1.,
                  context_tokens=8192, completion_tokens=128, train_presentation_seeds=[420, 421, 422, 423],
                  evaluation_presentation_seeds=[420, 777], thinking=False,
                  gate='all_eight_train_exemplars_exact_and_two_unseen_menu_presentations_exact',
                  full_workflow_or_generalization_claim=False, optimizer_run=False,
                  scope='Finite fresh-base skill memorization diagnostic;no heldout data,GRPO,recipe search or automatic extension')
    write_json(root / 'summary.json', result)
    seal(root)
    return result


def check_plan(root, anchor):
    if verify(root) != anchor:
        raise ValueError('owner skill plan differs')
    p = read_json(Path(root) / 'summary.json')
    if p.get('schema') != 'power-grid-env.skill-pilot-plan/v1' or p.get('steps') != 32 or p.get('optimizer_run') is not False:
        raise ValueError('unsupported finite skill plan')
    for f, sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f)) != sha:
            raise ValueError('skill pilot source changed: ' + f)
    data = SkillDataset(p['sources'])
    if p['lora'] != LoraSpec().model_dump() or p['learning_rate'] != .0001 or p['gradient_clip'] != 1. or p['context_tokens'] != 8192 or p['completion_tokens'] != 128:
        raise ValueError('unsupported pilot recipe')
    indices = data.pilot_indices()
    if p['exemplars'] != [dict(index=i, row_id=data.rows[i]['id'], skill=data.rows[i]['skill']) for i in indices]:
        raise ValueError('pilot exemplars differ')
    rng = random.Random(p['seed']); expected = []
    for repeat in range(4):
        order = list(indices); rng.shuffle(order)
        expected.extend(dict(index=i, row_id=data.rows[i]['id'], presentation_seed=420 + repeat) for i in order)
    if p['schedule'] != expected or p['evaluation_presentation_seeds'] != [420, 777] or p['thinking'] is not False:
        raise ValueError('pilot schedule/evaluation changed')
    return data, p


def evaluate(model, processor, data, plan, candidate):
    import torch
    rows = []
    model.eval()
    for exemplar in plan['exemplars']:
        i = exemplar['index']
        for seed in plan['evaluation_presentation_seeds']:
            if seed == 777 and data.rows[i]['skill'] != 'choice':
                continue
            messages, answer = data.messages(i, seed)
            inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                        return_dict=True, return_tensors='pt', enable_thinking=False)
            n = inputs['input_ids'].shape[1]
            if n + plan['completion_tokens'] > plan['context_tokens']:
                raise ValueError('pilot inference context exceeds budget')
            with torch.inference_mode():
                ids = model.generate(**inputs, do_sample=False, num_beams=1, max_new_tokens=plan['completion_tokens'],
                            use_cache=True, logits_to_keep=1, pad_token_id=processor.tokenizer.pad_token_id,
                            eos_token_id=processor.tokenizer.eos_token_id)
            raw = processor.tokenizer.decode(ids[0, n:], skip_special_tokens=True)
            result = dict(candidate=candidate, row_id=exemplar['row_id'], skill=exemplar['skill'],
                          presentation_seed=seed, expected=answer, completion=raw, **score(raw, answer))
            rows.append(result)
            print(json.dumps(result), flush=True)
    return rows


def run(plan_root, anchor, target, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh skill training output required')
    data, plan = check_plan(plan_root, anchor)
    from power_grid_env.qwen35_training import (local_target, load_native, adapter_state,
            restore_adapters, frozen_fingerprint, supervised_forward)
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.vlm_batches import local_qwen35_processor
    from benchmarks.engineering_train import save_optimizer
    import torch
    from safetensors.torch import save_file, load_file
    torch.set_num_threads(4); torch.manual_seed(plan['seed'])
    target_meta = local_target(target, plan['target_sha256'])
    processor = local_qwen35_processor(target_meta['local_checkpoint'], target_meta['config_sha256'])
    # Every used presentation is admitted BEFORE loading weights or optimizing.
    admission = []
    for entry in plan['schedule']:
        _, meta = data.batch(processor, entry['index'], entry['presentation_seed'])
        admission.append(meta)
    root.mkdir(parents=True)
    write_json(root / 'admission.json', dict(passed=True, checked_batches=len(admission), rows=admission))
    write_json(root / 'state.json', dict(phase='base_evaluation', complete=False))
    processor, model = load_native(target_meta, 4)
    core = package_fingerprint()
    before = evaluate(model, processor, data, plan, 'base')
    write_json(root / 'before.json', dict(complete=True, results=before))
    names = attach_language_lora(model, LoraSpec.model_validate(plan['lora']))
    frozen = frozen_fingerprint(model); initial = adapter_state(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=plan['learning_rate'], weight_decay=0.)
    history = []; visual_calls = [0]
    def count(*args):
        visual_calls[0] += 1
    hook = model.model.visual.register_forward_hook(count)
    for step, entry in enumerate(plan['schedule']):
        model.train()
        batch, meta = data.batch(processor, entry['index'], entry['presentation_seed'])
        optimizer.zero_grad(set_to_none=True)
        loss, logits = supervised_forward(model, batch)
        if not bool(torch.isfinite(loss)):
            raise ValueError('nonfinite skill loss')
        loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
            raise ValueError('missing/nonfinite skill gradients')
        grad = torch.nn.utils.clip_grad_norm_(params, plan['gradient_clip'], error_if_nonfinite=True)
        if float(grad) <= 0:
            raise ValueError('zero skill gradient')
        optimizer.step()
        if any(not bool(torch.isfinite(p).all()) for p in params):
            raise ValueError('nonfinite skill adapter')
        row = dict(step=step + 1, **meta, loss=float(loss.detach()), gradient_norm=float(grad))
        history.append(row)
        write_json(root / 'state.json', dict(phase='training', complete=False, completed_steps=step + 1, total_steps=32))
        print(json.dumps(row), flush=True)
        del loss, logits
    model.eval()
    if frozen_fingerprint(model) != frozen:
        raise ValueError('frozen base/vision changed')
    state = adapter_state(model)
    if all(torch.equal(v, initial[k]) for k, v in state.items()):
        raise ValueError('skill optimizer made no update')
    save_file(state, str(root / 'adapter.safetensors'))
    optimizer_names = save_optimizer(model, optimizer, root / 'optimizer.safetensors')
    with torch.inference_mode():
        expected_loss, expected_logits = supervised_forward(model, batch)
    restore_adapters(model, {k: torch.zeros_like(v) for k, v in state.items()})
    restore_adapters(model, load_file(str(root / 'adapter.safetensors')))
    with torch.inference_mode():
        actual_loss, actual_logits = supervised_forward(model, batch)
    if not torch.equal(expected_loss, actual_loss) or not torch.equal(expected_logits, actual_logits):
        raise ValueError('skill checkpoint roundtrip differs')
    write_json(root / 'state.json', dict(phase='after_evaluation', complete=False, completed_steps=32))
    after = evaluate(model, processor, data, plan, 'adapter32')
    hook.remove()
    for f, sha in plan['source_sha256'].items():
        if identity(Path(__file__).with_name(f)) != sha:
            raise ValueError('source changed during skill run')
    if package_fingerprint() != core or visual_calls[0] < 4:
        raise ValueError('core changed or visual pathway not exercised')
    result = dict(schema='power-grid-env.skill-pilot-result/v1', complete=True, plan_manifest_sha256=anchor,
                  core_sha256=core, steps=32, adapter_names=names, optimizer_parameter_names=optimizer_names,
                  frozen_base_unchanged=True, adapter_roundtrip_exact=True, visual_encoder_forward_calls=visual_calls[0],
                  history=history, before=before, after=after,
                  gate_passed=all(r['exact'] for r in after),
                  exact_by_skill={c: {s: dict(correct=sum(r['exact'] for r in values if r['skill'] == s),
                         total=sum(r['skill'] == s for r in values)) for s in ('binding', 'readiness', 'choice', 'perception')}
                         for c, values in [('base', before), ('adapter32', after)]},
                  full_workflow_or_generalization_claim=False,
                  scope='Eight train exemplars plus two unseen menu-order probes;finite skill diagnostic,not a deployment policy')
    write_json(root / 'summary.json', result)
    write_json(root / 'state.json', dict(phase='complete', complete=True, gate_passed=result['gate_passed']))
    seal(root)
    return {k: v for k, v in result.items() if k not in ('before', 'after', 'history')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['plan', 'run'])
    parser.add_argument('--out', required=True)
    parser.add_argument('--target-sha256')
    parser.add_argument('--target')
    parser.add_argument('--plan')
    parser.add_argument('--plan-manifest-sha256')
    a = parser.parse_args()
    if a.command == 'plan':
        if not a.target_sha256:
            parser.error('plan requires --target-sha256')
        print(json.dumps(create_plan(sources(), a.target_sha256, a.out)))
    else:
        if not all([a.target, a.plan, a.plan_manifest_sha256]):
            parser.error('run requires target,plan and owner plan manifest')
        print(json.dumps(run(a.plan, a.plan_manifest_sha256, a.target, a.out)))

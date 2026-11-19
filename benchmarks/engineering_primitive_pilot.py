"""Finite TRAIN-only primitive learning experiment, not a held-out benchmark.

Ten original inputs ×3 seeded passes; ten reordered-key probes are not trained.
No automatic extra epochs, checkpoint selection, teacher correction or GRPO.
"""
import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_primitive_admission import PrimitiveDataset
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_sft_runtime_v2 import NativePolicy
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_trainer_v2 import LoraSpec, attach_language_lora
from power_grid_env.study_artifacts import seal


def order(indices, seed=20261119):
    if len(indices) != 10 or len(set(indices)) != 10:
        raise ValueError('ten distinct TRAIN originals required')
    rng, result = random.Random(seed), []
    for _ in range(3):
        values = list(indices)
        rng.shuffle(values)
        result.extend(values)
    return result


def generated(policy, data, indices, originals, phase):
    results = []
    for i in indices:
        row = data.example(i)
        raw = policy(dict(messages=row['messages'], maximum_completion_tokens=128))
        try:
            correct = sha(bounded_json(raw)) == sha(row['answer'])
        except (ValueError, TypeError):
            correct = False
        value = dict(index=i, row_id=row['id'], skill=row['skill'], phase=phase,
                     group='train_original' if i in originals else 'untrained_key_order',
                     completion=raw, expected=row['answer'], correct=correct)
        results.append(value)
        print(json.dumps(dict(phase=phase, index=i, skill=row['skill'], correct=correct)), flush=True)
    return results


def run(admission, anchor, target, target_sha, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh primitive pilot required')
    data = PrimitiveDataset(admission, anchor, target_sha)
    indices, nuisance = data.pilot_indices()
    schedule = order(indices)
    from power_grid_env.qwen35_training import (local_target, load_native, frozen_fingerprint,
        supervised_forward, adapter_state, restore_adapters)
    from power_grid_env.evidence import package_fingerprint
    import torch
    from safetensors.torch import save_file, load_file
    torch.manual_seed(20261119)
    processor, model = load_native(local_target(target, target_sha), 4)
    spec = LoraSpec()
    names = attach_language_lora(model, spec)
    frozen = frozen_fingerprint(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=.0001, weight_decay=0.)
    identity = dict(admission_manifest_sha256=anchor, target_sha256=target_sha, core_sha256=package_fingerprint(),
                    source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                   for name in ('engineering_primitive_pilot.py', 'engineering_primitive_admission.py',
                                                'engineering_primitive_curriculum.py', 'engineering_sft_runtime_v2.py',
                                                'engineering_trainer_v2.py', 'engineering_staged_data.py')})
    recipe = dict(seed=20261119, learning_rate=.0001, lora=spec.model_dump(), indices=indices,
                  nuisance_indices=nuisance, schedule=schedule, steps=30, gradient_clip=1.,
                  objective='assistant_only_uniform_cross_entropy', optimizer_initialization='fresh_base_and_AdamW',
                  stop_rule='fixed30_updates_no_automatic_extension')
    root.mkdir(parents=True)
    write_json(root/'plan.json',dict(identity=identity,recipe=recipe))
    history = []
    try:
        actor = NativePolicy(processor,model)
        before = generated(actor,data,indices+nuisance,indices,'before')
        write_json(root/'before.json',before)
        for step,index in enumerate(schedule):
            model.eval()
            batch,meta=data.batch(processor,index)
            optimizer.zero_grad(set_to_none=True)
            loss,logits=supervised_forward(model,batch)
            loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
                raise ValueError('missing/nonfinite primitive gradients')
            grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
            if float(grad)<=0:
                raise ValueError('zero primitive gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(p).all()) for p in params):
                raise ValueError('nonfinite primitive weights')
            history.append(dict(step=step,index=index,row_id=data.rows[index]['id'],**meta,loss=float(loss.detach()),gradient_norm=float(grad)))
            print(json.dumps(dict(step=step+1,total=30,skill=meta['skill'],loss=history[-1]['loss'])),flush=True)
            write_json(root/'partial.json',dict(complete=False,history=history))
            del loss,logits
        after=generated(actor,data,indices+nuisance,indices,'after')
        if frozen_fingerprint(model)!=frozen:
            raise ValueError('primitive frozen base changed')
        state=adapter_state(model)
        save_file(state,str(root/'adapter.safetensors'))
        batch,_=data.batch(processor,indices[0])
        with torch.inference_mode(): expected_loss,expected=supervised_forward(model,batch)
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()})
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode(): actual_loss,actual=supervised_forward(model,batch)
        if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):
            raise ValueError('primitive adapter roundtrip differs')
        result=dict(schema='power-grid-env.primitive-native-pilot/v1',complete=True,identity=identity,recipe=recipe,
                    before=before,after=after,history=history,adapter_names=names,
                    before_correct=sum(r['correct'] for r in before),after_correct=sum(r['correct'] for r in after),
                    per_candidate_targets=20,train_targets=10,train_base_networks=1,
                    before_by_skill=dict(Counter(r['skill'] for r in before if r['correct'])),
                    after_by_skill=dict(Counter(r['skill'] for r in after if r['correct'])),
                    primitive_learning_gate_passed=all(r['correct'] for r in after),
                    full_workflow_quality_gate_passed=False,independent_heldout_evaluated=False,
                    frozen_base_unchanged=True,adapter_roundtrip_exact=True,inference_release=False,
                    scope='Fixed30-update tiny TRAIN primitive learning and key-order retention; '
                          'not unseen-family, workflow, vision, or production qualification. No resume in this finite pilot.')
        write_json(root/'summary.json',result)
        seal(root)
        return {k:v for k,v in result.items() if k not in ('before','after','history','adapter_names')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,identity=identity,error=type(exc).__name__,detail=str(exc)[:1000]))
        seal(root)
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('admission','manifest-sha256','target','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.admission,a.manifest_sha256,a.target,a.target_sha256,a.out)))

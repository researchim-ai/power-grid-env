"""Native TRAIN supervision admission and label-free held-out prompt admission."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_primitive_curriculum import load, sha
from benchmarks.engineering_staged_data import pack
from power_grid_env.study_artifacts import read_json, seal, verify


FILES = ('engineering_primitive_admission.py', 'engineering_primitive_curriculum.py',
         'engineering_family_registry.py', 'engineering_staged_data.py', 'engineering_value_loss.py')


def sources():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in FILES}


def qualify(source, anchor, target, target_sha, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh primitive admission required')
    train, owner, summary = load(source, anchor)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    spec = local_target(target, target_sha)
    processor = local_qwen35_processor(spec['local_checkpoint'], spec['config_sha256'])
    checks, failures = [], []
    for index, row in enumerate(train):
        try:
            _, meta = pack(processor, row)
            checks.append(dict(id=row['id'], split='train', index=index, supervised=True, **meta))
        except (ValueError, RuntimeError) as exc:
            failures.append(dict(id=row['id'], error=str(exc)[:500]))
    for row in owner:
        # Do not append or tokenize held-out answers in the SFT path.
        inputs = processor.apply_chat_template(row['messages'], tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt', enable_thinking=False)
        n = inputs['input_ids'].shape[1]
        if n+128 > 8192:
            failures.append(dict(id=row['id'], error='heldout prompt context exceeded'))
        checks.append(dict(id=row['id'], split=row['split'], supervised=False, prompt_tokens=n, label_tokens=0))
    root.mkdir(parents=True)
    write_json(root/'checks.json', checks)
    receipt = dict(schema='power-grid-env.primitive-native-admission/v1', source=str(Path(source).resolve()), source_manifest_sha256=anchor,
                   target_sha256=target_sha, passed=not failures, failures=failures,
                   supervised_train_targets=len(train), prompt_only_heldout_targets=len(owner),
                   maximum_prompt_tokens=max(c['prompt_tokens'] for c in checks),
                   source_sha256=sources(), weights_loaded=False, optimizer_run=False,
                   eligible_for_train_only_primitive_calibration=not failures,
                   full_workflow_quality_gate_passed=False,
                   scope='All train labels/payloads and label-free dev/test prompts; replayed API effects and exact analytic comparisons. Not policy quality.')
    write_json(root/'summary.json', receipt)
    seal(root)
    return receipt


class PrimitiveDataset:
    def __init__(self, admission, anchor, target_sha):
        root = Path(admission)
        if verify(root) != anchor:
            raise ValueError('primitive admission manifest differs')
        plan = read_json(root/'summary.json')
        if (plan.get('schema') != 'power-grid-env.primitive-native-admission/v1' or
                plan.get('target_sha256') != target_sha or plan.get('passed') is not True or plan.get('failures') or
                plan.get('source_sha256') != sources() or plan.get('eligible_for_train_only_primitive_calibration') is not True):
            raise ValueError('passing matching primitive admission required')
        train, owner, summary = load(plan['source'], plan['source_manifest_sha256'])
        checks = read_json(root/'checks.json')
        expected = {(r['id'], r['split'], r['split']=='train') for r in train+owner}
        actual = {(r['id'], r['split'], r['supervised']) for r in checks}
        if actual != expected or len(checks) != len(expected) or plan['supervised_train_targets'] != len(train):
            raise ValueError('complete separated native inventory required')
        if any(c.get('label_tokens') != 0 for c in checks if not c['supervised']):
            raise ValueError('heldout answers were supervised')
        # Only TRAIN rows become part of the dataset exposed to the optimizer.
        self.rows, self.plan = train, plan

    def example(self, index):
        if type(index) is not int or not 0 <= index < len(self.rows):
            raise ValueError('bounded train index required')
        row = self.rows[index]
        if row['split'] != 'train':
            raise ValueError('heldout row cannot enter primitive SFT')
        return copy.deepcopy(row)

    def batch(self, processor, index):
        return pack(processor, self.example(index))

    def pilot_indices(self):
        base = min(r['base_project_id'] for r in self.rows)
        def find(skill, member, group=None, variant='original'):
            return next(i for i,r in enumerate(self.rows) if r['base_project_id']==base and r['skill']==skill
                        and r['member']==member and r['nuisance_variant']==variant and (group is None or r['pair_group']==group))
        threshold = min(r['pair_group'] for r in self.rows if r['base_project_id']==base and r['skill']=='threshold')
        indices = [find('state_reading', m) for m in ('0','2')]
        indices += [find('tool_effect', m) for m in ('initial_validate','initial_simulate','fresh_mutation','rejected_mutation')]
        indices += [find('threshold', m, threshold) for m in ('below','equal','above','missing')]
        transfer = [find(self.rows[i]['skill'],self.rows[i]['member'],self.rows[i]['pair_group'],'reordered_keys') for i in indices]
        if len(set(indices+transfer)) != 20:
            raise ValueError('ten original and ten distinct nuisance probes required')
        return indices, transfer


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for key in ('source','manifest-sha256','target','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(qualify(a.source,a.manifest_sha256,a.target,a.target_sha256,a.out)))

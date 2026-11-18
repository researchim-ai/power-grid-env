"""Pinned, replay-checked train action batches and bounded adapter qualification.

Provisional owner-teacher supervision, not independently qualified gold SFT.
"""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks import engineering_agent as env
from benchmarks.engineering_rollout import rollout, parse_action, strict_json
from benchmarks.engineering_qwen_policy import pack
from power_grid_env.study_artifacts import read_json, verify, seal
from power_grid_env.evidence import package_fingerprint


class ActionDataset:
    def __init__(self, root, manifest_sha256, target_sha256):
        self.root = Path(root)
        if verify(self.root) != manifest_sha256:
            raise ValueError('owner corpus manifest differs')
        summary = read_json(self.root/'summary.json')
        if (summary.get('schema') != 'power-grid-env.engineering-action-supervision/v1' or
                summary.get('split') != 'train' or summary.get('model_target_sha256') != target_sha256 or
                summary.get('episode_sha256') != hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest() or
                summary.get('eligible_for_gold_sft') is not False):
            raise ValueError('incompatible provisional train corpus identity')
        self.context = summary['context_tokens']
        if type(self.context) is not int or not 1024 <= self.context <= 32768:
            raise ValueError('invalid context budget')
        self.rows = [strict_json(line) for line in (self.root/'train-actions.jsonl').read_text().splitlines()]
        if len(self.rows) != summary['examples'] or not self.rows:
            raise ValueError('corpus count differs')
        train = {t['id']: t for t in env.registry() if t['split'] == 'train'}
        grouped = {}
        expected_keys = {'task_id','split','family_sha256','messages','completion','prompt_tokens',
                         'label_tokens','total_tokens','loss_mask','eligible_for_gold_sft'}
        for row in self.rows:
            if not isinstance(row, dict) or set(row) != expected_keys:
                raise ValueError('unexpected record fields')
            task = train.get(row['task_id'])
            if (task is None or row['split'] != 'train' or row['family_sha256'] != task['family_sha256'] or
                    row['eligible_for_gold_sft'] is not False or
                    row['loss_mask'] != dict(prompt_tokens=0,assistant_completion=1)):
                raise ValueError('non-train task or invalid record identity/mask')
            counts = [row[k] for k in ('prompt_tokens','label_tokens','total_tokens')]
            if (any(type(n) is not int or n <= 0 for n in counts) or
                    counts[0]+counts[1] != counts[2] or counts[2] > self.context):
                raise ValueError('invalid token counts')
            if not isinstance(row['completion'],dict) or set(row['completion']) != {'role','content'} or row['completion']['role'] != 'assistant':
                raise ValueError('assistant completion required')
            parse_action(row['completion']['content'], env.public_task(task)['tools'])
            grouped.setdefault(task['id'],[]).append(row)
        if set(grouped) != set(train) or summary['tasks'] != len(train):
            raise ValueError('complete train task inventory required')
        # Re-execute public transitions, rejecting fabricated history, reward or solver outputs.
        for identifier, records in grouped.items():
            iterator = iter(records)
            def replay(messages):
                row = next(iterator)
                if row['messages'] != messages:
                    raise ValueError('public history differs from owner replay')
                return row['completion']['content']
            result = rollout(identifier, replay)
            if not result['success'] or len(result['turns']) != len(records):
                raise ValueError('incomplete or inconsistent training trajectory')
        self.manifest_sha256 = manifest_sha256

    def __len__(self):
        return len(self.rows)

    def batch(self, processor, index):
        import torch
        if type(index) is not int or not 0 <= index < len(self):
            raise ValueError('training index outside corpus')
        row = self.rows[index]
        prompt, length = pack(processor, row['messages'], self.context, 1)
        batch = processor.apply_chat_template(row['messages']+[row['completion']], tokenize=True,
            add_generation_prompt=False, return_dict=True, return_tensors='pt', enable_thinking=False)
        ids = batch['input_ids']
        if (ids.ndim != 2 or ids.shape[0] != 1 or
                not torch.equal(ids[:,:length],prompt['input_ids']) or
                length != row['prompt_tokens'] or ids.shape[1] != row['total_tokens'] or
                ids.shape[1]-length != row['label_tokens'] or ids.shape[1] > self.context or
                not bool((batch['attention_mask'] == 1).all())):
            raise ValueError('native token identity/counts or attention differ')
        labels = ids.clone()
        labels[:,:length] = -100
        batch['labels'] = labels
        if int((labels != -100).sum()) != row['label_tokens']:
            raise ValueError('assistant-only label mask differs')
        return batch, dict(index=index,task_id=row['task_id'],prompt_tokens=length,
                           label_tokens=row['label_tokens'],total_tokens=row['total_tokens'])


def qualify(target_path, target_sha, corpus, manifest, out, train_step=False, index=0):
    import torch
    from power_grid_env.qwen35_training import (local_target, load_native, AdapterPlan,
        attach_adapters, adapter_state, frozen_fingerprint, supervised_forward, restore_adapters)
    from power_grid_env.vlm_batches import local_qwen35_processor
    from safetensors.torch import save_file, load_file
    root = Path(out)
    if root.exists():
        raise ValueError('fresh qualification output required')
    dataset = ActionDataset(corpus,manifest,target_sha)
    if train_step and (type(index) is not int or not 0 <= index < len(dataset)):
        raise ValueError('training index outside corpus')
    target = local_target(target_path,target_sha)
    processor = local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    records = []
    for i in range(len(dataset)):
        batch, meta = dataset.batch(processor,i)
        records.append(meta)
    receipt = dict(schema='power-grid-env.engineering-sft-qualification/v1', examples=len(records),
        masked_prompt_tokens=sum(r['prompt_tokens'] for r in records),
        supervised_tokens=sum(r['label_tokens'] for r in records),records=records,
        corpus_manifest_sha256=manifest,target_sha256=target_sha,
        core_sha256=package_fingerprint(),
        environment_sha256=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest(),
        loader_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        eligible_for_gold_sft=False,optimizer_steps=0,
        scope='Train-only batching/adapter plumbing; no learned engineering competence, visual training or GRPO claim')
    if train_step:
        torch.manual_seed(42)
        processor, model = load_native(target,4)
        plan = AdapterPlan(indices=[index])
        names = attach_adapters(model,plan)
        frozen_before = frozen_fingerprint(model)
        initial = adapter_state(model)
        params = [p for p in model.parameters() if p.requires_grad]
        batch, meta = dataset.batch(processor,index)
        optimizer = torch.optim.AdamW(params,lr=plan.learning_rate,weight_decay=0.)
        optimizer.zero_grad(set_to_none=True)
        loss, logits = supervised_forward(model,batch)
        loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
            raise ValueError('missing/nonfinite adapter gradients')
        grad = torch.nn.utils.clip_grad_norm_(params,plan.gradient_clip,error_if_nonfinite=True)
        if float(grad) <= 0:
            raise ValueError('zero gradient')
        optimizer.step()
        state = adapter_state(model)
        if any(not bool(torch.isfinite(v).all()) for v in state.values()) or all(torch.equal(initial[k],v) for k,v in state.items()):
            raise ValueError('invalid or unchanged adapter update')
        if frozen_fingerprint(model) != frozen_before:
            raise ValueError('frozen model changed')
        with torch.inference_mode():
            updated_loss, expected = supervised_forward(model,batch)
        root.mkdir(parents=True,exist_ok=False)
        save_file(state,str(root/'adapter.safetensors'))
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()})
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode():
            restored_loss, restored = supervised_forward(model,batch)
        if not torch.equal(expected,restored) or not torch.equal(updated_loss,restored_loss):
            raise ValueError('adapter roundtrip differs')
        receipt.update(optimizer_steps=1,index=index,training_plan=plan.model_dump(mode='json'),loss_before=float(loss.detach()),
            loss_after=float(updated_loss),gradient_norm=float(grad),adapter_names=names,
            frozen_unchanged=True,adapter_roundtrip_exact=True,
            note='One-sample engineering action optimizer qualification; not a full training run')
    else:
        root.mkdir(parents=True,exist_ok=False)
    (root/'summary.json').write_text(json.dumps(receipt,indent=2)+'\n')
    seal(root)
    return {k:v for k,v in receipt.items() if k not in ('records','adapter_names')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True)
    p.add_argument('--corpus',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    p.add_argument('--train-step',action='store_true');p.add_argument('--index',type=int,default=0)
    a=p.parse_args();print(json.dumps(qualify(a.target,a.target_sha256,a.corpus,a.manifest_sha256,a.out,a.train_step,a.index)))

if __name__=='__main__':main()

"""Independently decode supervised tokens and verify complete tool semantics.

Prefix/mask correctness alone cannot prove a template retained required args.
This gate checks actual rendered labels,not a parallel hand-written serializer.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_protocol import parse_native
from benchmarks.engineering_rollout import parse_action
from power_grid_env.study_artifacts import seal


def verify_target(processor,batch,expected,contract):
    import torch
    labels=batch['labels'];ids=batch['input_ids'];active=labels!=-100
    if labels.shape!=ids.shape or labels.ndim!=2 or labels.shape[0]!=1 or not bool(active.any()) or bool(active[0,0]):raise ValueError('causal supervised target required')
    indices=torch.nonzero(active[0],as_tuple=False).flatten()
    if not torch.equal(indices,torch.arange(int(indices[0]),ids.shape[1],device=indices.device)) or not torch.equal(labels[active],ids[active]):
        raise ValueError('one exact assistant suffix required')
    raw=processor.tokenizer.decode(ids[active].tolist(),skip_special_tokens=True)
    action=parse_action(raw,[expected['name']]) if contract.protocol=='json-action-v1' else parse_native(raw,[expected['name']])
    if action!=expected:raise ValueError('actual decoded labels changed tool name or argument values')
    return dict(decoded_completion_sha256=hashlib.sha256(raw.encode()).hexdigest(),argument_fields=len(expected['arguments']),
        argument_value_tokens=sum(len(processor.tokenizer.encode(value,add_special_tokens=False)) for value in expected['arguments'].values()))


def qualify(train,manifest,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh semantic admission required')
    contract=strict_contract();data=ProceduralDataset(train,manifest,contract)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    rows=[];failures=[];arguments=Counter()
    for index,(e,turn) in enumerate(data.targets):
        expected=data.episodes[e]['events'][turn]['action']
        try:
            batch,meta=data.batch(processor,index);semantic=verify_target(processor,batch,expected,contract)
            rows.append(dict(index=index,action=expected['name'],**semantic));arguments[expected['name']]+=semantic['argument_fields']
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=index,error=str(exc)[:1000]))
        if (index+1)%256==0:print(json.dumps(dict(checked=index+1,total=len(data),failures=len(failures))),flush=True)
    r=dict(schema='power-grid-env.target-semantic-admission/v1',passed=bool(rows) and not failures,targets=len(data),checked_targets=len(rows),failures=failures,
        train_manifest_sha256=manifest,target_sha256=target_sha,contract=contract.model_dump(),argument_fields_by_action=dict(arguments),
        argument_value_tokens=sum(r['argument_value_tokens'] for r in rows),rows=rows,weights_loaded=False,optimizer_run=False,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Every actual supervised completion retains full tool name and arguments;not model quality or argument-generation success')
    write_json(root/'summary.json',r);seal(root);return {k:v for k,v in r.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('train','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+flag,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.train,a.manifest_sha256,a.target,a.target_sha256,a.out)))

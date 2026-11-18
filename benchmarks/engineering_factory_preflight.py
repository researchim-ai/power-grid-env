"""All-target native processor admission; no truncation or dropped examples."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_protocol import RunContract
from power_grid_env.study_artifacts import seal


def qualify(root,manifest,target_path,target_sha,out,protocol='qwen-native-v1',context_tokens=24576):
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    dest=Path(out)
    if dest.exists():raise ValueError('fresh preflight output required')
    contract=RunContract(protocol=protocol,maximum_actions=24,context_tokens=context_tokens)
    dataset=ProceduralDataset(root,manifest,contract)
    target=local_target(target_path,target_sha)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    errors=[];rows=[];kinds=Counter();actions=Counter()
    for index in range(len(dataset)):
        try:
            batch,meta=dataset.batch(processor,index)
            episode,turn=dataset.targets[index];episode=dataset.episodes[episode]
            action=episode['events'][turn]['action']['name'];kind=dataset.scenarios[episode['task_id']]['kind']
            actions[action]+=1;kinds[kind]+=1
            rows.append(dict(**meta,action=action,kind=kind))
        except (ValueError,RuntimeError) as exc:errors.append(dict(index=index,error=str(exc)[:1000]))
    result=dict(schema='power-grid-env.procedural-preflight/v1',passed=not errors,targets=len(dataset),checked_targets=len(rows),failures=errors,
        contract=contract.model_dump(),contract_sha256=contract.fingerprint(),train_manifest_sha256=manifest,target_sha256=target_sha,
        actions=dict(actions),kinds=dict(kinds),maximum_prompt_tokens=max((r['prompt_tokens'] for r in rows),default=0),
        maximum_label_tokens=max((r['label_tokens'] for r in rows),default=0),supervised_tokens=sum(r['label_tokens'] for r in rows),
        loader_sha256=hashlib.sha256(Path(__file__).with_name('engineering_factory.py').read_bytes()).hexdigest(),
        protocol_sha256=hashlib.sha256(Path(__file__).with_name('engineering_protocol.py').read_bytes()).hexdigest(),
        weights_loaded=False,eligible_for_gold_sft=False,scope='Synthetic profile token/mask/replay admission,not learned-policy quality or external physical validation',rows=rows)
    dest.mkdir(parents=True,exist_ok=False);write_json(dest/'summary.json',result);seal(dest)
    return {k:v for k,v in result.items() if k!='rows'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--train',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True);p.add_argument('--out',required=True);p.add_argument('--protocol',choices=['json-action-v1','qwen-native-v1','qwen-native-strict-v1'],default='qwen-native-v1')
    p.add_argument('--context-tokens',type=int,default=24576)
    a=p.parse_args();print(json.dumps(qualify(a.train,a.manifest_sha256,a.target,a.target_sha256,a.out,a.protocol,a.context_tokens)))

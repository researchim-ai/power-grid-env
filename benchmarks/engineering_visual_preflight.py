"""Native VLM admission of every paired public frame; no weight loading."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_recovery import episode_messages
from benchmarks.engineering_visual_data import public_snapshot,render,pack_visual,FONT
from power_grid_env.study_artifacts import verify,read_json,seal


def qualify(visual,visual_manifest,train,train_manifest,target_path,target_sha,out,protocol='qwen-native-strict-v1'):
    import torch
    torch.set_num_threads(4)
    if verify(visual)!=visual_manifest:raise ValueError('owner visual manifest differs')
    summary=read_json(Path(visual)/'summary.json')
    if summary['schema']!='power-grid-env.public-visual-train/v1' or summary['train_manifest_sha256']!=train_manifest:
        raise ValueError('visual/train identities differ')
    if summary['font_sha256']!=hashlib.sha256(Path(FONT).read_bytes()).hexdigest():raise ValueError('font revision differs')
    if summary['renderer_sha256']!=hashlib.sha256(Path(__file__).with_name('engineering_visual_data.py').read_bytes()).hexdigest():raise ValueError('renderer revision differs')
    root=Path(out)
    if root.exists():raise ValueError('fresh visual preflight output required')
    contract=RunContract(protocol=protocol,maximum_actions=24,context_tokens=24576)
    data=ProceduralDataset(train,train_manifest,contract);episodes={e['episode_id']:e for e in data.episodes}
    records=[json.loads(line) for line in (Path(visual)/'records.jsonl').read_text().splitlines()]
    if len(records)!=summary['records'] or len(records)!=len(episodes) or len({r['episode_id'] for r in records})!=len(records):raise ValueError('complete one-frame-per-episode coverage required')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    target=local_target(target_path,target_sha);processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    checked=[];failures=[]
    for index,record in enumerate(records):
        try:
            episode=episodes[record['episode_id']]
            messages,event=next((m,e) for m,e in episode_messages(episode) if e['turn']==record['turn'])
            if record['split']!='train' or record['messages']!=messages or record['completion']!=event['action'] or event['source']!='public_expert':raise ValueError('public image label provenance differs')
            expected=render(public_snapshot(messages));expected_sha=hashlib.sha256(expected).hexdigest()
            path=(Path(visual)/record['image']).resolve()
            if not path.is_relative_to(Path(visual).resolve()/'images') or expected_sha!=record['image_sha256'] or hashlib.sha256(path.read_bytes()).hexdigest()!=expected_sha:
                raise ValueError('image is not the declared public observation')
            batch,meta=pack_visual(processor,record,path,contract)
            checked.append(dict(index=index,episode_id=record['episode_id'],**meta))
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=index,error=str(exc)[:1000]))
        if (index+1)%64==0:print(json.dumps(dict(checked=index+1,total=len(records),failures=len(failures))),flush=True)
    result=dict(schema='power-grid-env.public-visual-preflight/v1',passed=not failures,records=len(records),checked_records=len(checked),failures=failures,
        visual_manifest_sha256=visual_manifest,train_manifest_sha256=train_manifest,target_sha256=target_sha,contract=contract.model_dump(),
        maximum_prompt_tokens=max((r['prompt_tokens'] for r in checked),default=0),rows=checked,weights_loaded=False,eligible_for_gold_sft=False,
        scope='Public-frame pixel/prefix/label admission only;no VLM training,visual-policy success or image-only grounding score')
    root.mkdir(parents=True,exist_ok=False);write_json(root/'summary.json',result);seal(root)
    return {k:v for k,v in result.items() if k!='rows'}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('visual','visual-manifest-sha256','train','train-manifest-sha256','target','target-sha256','out'):p.add_argument('--'+flag,required=True)
    p.add_argument('--protocol',choices=['qwen-native-v1','qwen-native-strict-v1'],default='qwen-native-strict-v1')
    a=p.parse_args();print(json.dumps(qualify(a.visual,a.visual_manifest_sha256,a.train,a.train_manifest_sha256,a.target,a.target_sha256,a.out,a.protocol)))

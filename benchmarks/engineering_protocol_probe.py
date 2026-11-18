"""One train-case paired protocol smoke; never a heldout capability benchmark."""
import argparse
import json
from pathlib import Path
from benchmarks import engineering_agent as env
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_rollout import ProcessPolicy,rollout
from power_grid_env.study_artifacts import seal


def probe(python,target,target_sha,out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    task=next(t for t in env.registry() if t['split']=='train' and t['kind']=='diagnose')
    rows=[]
    for protocol in ('json-action-v1','qwen-native-v1'):
        contract=RunContract(protocol=protocol,maximum_actions=4)
        command=[python,'-m','benchmarks.engineering_protocol_policy','--target',target,'--target-sha256',target_sha,
            '--protocol',protocol,'--maximum-actions','4','--repeat-limit','2']
        policy=ProcessPolicy(command,timeout=180)
        try:row=rollout(task['id'],policy,max_turns=contract.maximum_actions,repeat_limit=contract.repeat_limit)
        finally:policy.close()
        record=dict(contract=contract.model_dump(),contract_sha256=contract.fingerprint(),candidate=policy.identity,row=row)
        (root/(protocol+'.json')).write_text(json.dumps(record,indent=2)+'\n');rows.append(record)
    summary=dict(schema='power-grid-env.protocol-train-probe/v1',target_sha256=target_sha,task_id=task['id'],split='train',
        results=[dict(protocol=r['contract']['protocol'],success=r['row']['success'],stop=r['row']['stop_reason'],calls=r['row']['calls']) for r in rows],
        scope='Single authored train diagnosis,base weights;not SFT,tiny-overfit gate or heldout protocol selection')
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');seal(root)
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--python',required=True);p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(probe(a.python,a.target,a.target_sha256,a.out)))

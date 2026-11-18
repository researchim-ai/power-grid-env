"""Independent distribution/duplicate/coverage audit of admitted supervision.

Different successful continuations of the same public state are alternatives,
not automatically corrupt labels. No evaluation traces enter training here.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_protocol import render_history
from benchmarks.engineering_recovery import episode_messages
from power_grid_env.study_artifacts import verify,read_json,seal


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def audit(train,anchor,preflight,preflight_anchor,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh supervision audit required')
    data=ProceduralDataset(train,anchor,strict_contract())
    if verify(preflight)!=preflight_anchor:raise ValueError('owner preflight differs')
    pf=read_json(Path(preflight)/'summary.json')
    if not pf['passed'] or pf['train_manifest_sha256']!=anchor or pf['contract_sha256']!=strict_contract().fingerprint():raise ValueError('preflight profile differs')
    metadata={r['index']:r for r in pf['rows']}
    if set(metadata)!=set(range(len(data))):raise ValueError('complete target metadata required')
    inputs=defaultdict(Counter);pairs=Counter();actions=Counter();kinds=Counter();tasks=Counter();argument_slots=Counter();rows=[];index=0
    # Same episode/turn ordering as ProceduralDataset.targets;never count injected actions as labels.
    for episode in data.episodes:
        kind=data.scenarios[episode['task_id']]['kind']
        for messages,event in episode_messages(episode):
            if event['source']!='public_expert':continue
            rendered,kwargs=render_history(messages,data.contract);ih=digest(dict(messages=rendered,kwargs=kwargs));ah=digest(event['action'])
            inputs[ih][ah]+=1;pairs[(ih,ah)]+=1;actions[event['action']['name']]+=1;kinds[kind]+=1;tasks[episode['task_id']]+=1
            argument_slots[event['action']['name']]+=len(event['action']['arguments'])
            rows.append(dict(index=index,task_id=episode['task_id'],family_sha256=episode['family_sha256'],kind=kind,action=event['action']['name'],
                argument_fields=len(event['action']['arguments']),input_sha256=ih,action_sha256=ah,**{k:metadata[index][k] for k in ('prompt_tokens','label_tokens')}));index+=1
    if index!=len(data):raise ValueError('target order/denominator differs')
    lengths=[r['prompt_tokens'] for r in rows];alternatives=[dict(input_sha256=h,distinct_valid_targets=len(v),occurrences=sum(v.values())) for h,v in inputs.items() if len(v)>1]
    r=dict(schema='power-grid-env.supervision-audit/v1',train_manifest_sha256=anchor,preflight_manifest_sha256=preflight_anchor,
        targets=len(data),episodes=len(data.episodes),tasks=len(data.scenarios),base_networks=len({s['base_project_id'] for s in data.scenarios.values()}),
        families=len({s['family_sha256'] for s in data.scenarios.values()}),unique_rendered_inputs=len(inputs),unique_input_action_pairs=len(pairs),duplicate_pairs=len(data)-len(pairs),
        states_with_multiple_valid_targets=len(alternatives),alternatives=alternatives,actions=dict(actions),kinds=dict(kinds),argument_fields=dict(argument_slots),
        prompt_tokens_quantiles={str(q):float(np.percentile(lengths,q)) for q in (0,50,90,95,99,100)},supervised_tokens=sum(r['label_tokens'] for r in rows),
        parameterized_targets=sum(r['argument_fields']>0 for r in rows),no_argument_targets=sum(r['argument_fields']==0 for r in rows),
        all_targets_physically_replayed=True,rows=rows,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Distribution/redundancy audit;multiple valid alternatives are not label errors;not model accuracy or proof of sufficient coverage')
    write_json(root/'summary.json',r);seal(root);return {k:v for k,v in r.items() if k not in ('rows','alternatives')}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('train','manifest-sha256','preflight','preflight-manifest-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(audit(a.train,a.manifest_sha256,a.preflight,a.preflight_manifest_sha256,a.out)))

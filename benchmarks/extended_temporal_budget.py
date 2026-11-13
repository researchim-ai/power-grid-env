"""Bounded Cauchy refinement, retaining every attempted time step.

This is a temporal self-consistency budget, not an absolute continuous-time
error certificate: both solvers share the mathematical integration scheme.
"""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.numerical_replay import assert_replay_equal
from benchmarks.extended_closed_loop_acceptance import NAMES,document,producer,compare,caps
from benchmarks.extended_closed_loop_reference import run as reference,channels
from benchmarks.extended_closed_loop_refinement import assessment
from power_grid_env.switching_drive import run_switching_drive
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

STEPS=(2.5e-6,1.25e-6,6.25e-7)


def arrays(p,r,label):
    return np.array([r['channels'][k] for k in channels(p)]).T.tolist() if label=='primary' else r['rows']


def change_budget(p,before,after):
    keys=channels(p);limits=caps(p);error=np.max(abs(np.asarray(before)-np.asarray(after)),axis=0)
    return dict(passed=bool(all(error[i]<=limits[k] for i,k in enumerate(keys))),errors=dict(zip(keys,error.tolist())),limits=limits)


def run(source,refined,out,replay=False):
    source=Path(source);refined=Path(refined);out=Path(out)
    identities=dict(qualification=verify(source),refinement=verify(refined));current=producer()
    if read_json(source/'producer.json')!=current or read_json(refined/'producer.json')['qualification']!=current or read_json(refined/'producer.json')['source_manifest_sha256']!=identities['qualification']:raise ValueError('temporal source producer differs')
    if not read_json(source/'summary.json')['passed'] or not read_json(refined/'summary.json')['passed']:raise ValueError('temporal source failed')
    before=dict(producer=current,source_manifests=identities,benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),refinement_sha256=hashlib.sha256(Path(__file__).with_name('extended_closed_loop_refinement.py').read_bytes()).hexdigest())
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('temporal producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            assert_replay_equal(read_json(out/name),value)
        else:(out/name).write_bytes(encoded(value))
    rows=[];trajectories=0
    for name in NAMES:
        p=document(name);previous={};earlier={};attempts=[]
        for label in ['primary','reference']:
            previous[label]=arrays(p,read_json(refined/f'{name}-{label}.json'),label);earlier[label]=arrays(p,read_json(source/f'{name}-fine-{label}.json'),label)
        for index,h in enumerate(STEPS):
            study=dict(p,max_step_s=h);a=run_switching_drive(study);b=reference(study);trajectories+=2
            store(f'{name}-{index}-study.json',study);store(f'{name}-{index}-primary.json',a);store(f'{name}-{index}-reference.json',b)
            checks={};values={}
            for label,r in [('primary',a),('reference',b)]:
                values[label]=arrays(study,r,label);checks[label]=dict(change=change_budget(study,previous[label],values[label]),reduction=assessment(study,earlier[label],previous[label],values[label]))
            cross=compare(study,a,b);passed=cross['passed'] and all(c['change']['passed'] and c['reduction']['passed'] for c in checks.values());attempt=dict(step_s=h,passed=passed,cross_comparison=cross,temporal_checks=checks);attempts.append(attempt)
            print(encoded(dict(case=name,attempt=attempt)).decode(),flush=True)
            if passed:break
            earlier=previous;previous=values
        rows.append(dict(case=name,passed=passed,selected_step_s=h if passed else None,attempts=attempts))
    result=dict(schema='power-grid-env.extended-temporal-budget/v1',passed=all(r['passed'] for r in rows),rows=rows,additional_trajectories=trajectories,step_candidates_s=list(STEPS),scope='fixed physical Cauchy-change limits and decreasing differences in each group; independently solved at each attempted step; not absolute continuous-time error or universal step recommendation',superiority_established=False)
    store('summary.json',result)
    if producer()!=current or verify(source)!=identities['qualification'] or verify(refined)!=identities['refinement'] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=before['benchmark_sha256'] or hashlib.sha256(Path(__file__).with_name('extended_closed_loop_refinement.py').read_bytes()).hexdigest()!=before['refinement_sha256']:raise ValueError('temporal producer/source changed')
    receipt=verify(out) if replay else seal(out)
    if replay and receipt!=manifest:raise ValueError('temporal artifact changed')
    print(encoded(dict(passed=result['passed'],manifest_sha256=receipt,replayed=replay)).decode(),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--refined',required=True);group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');a=parser.parse_args();run(a.source,a.refined,a.verify or a.out,bool(a.verify))

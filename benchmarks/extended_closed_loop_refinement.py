"""Third step level for an already sealed extended closed-loop qualification."""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.numerical_replay import assert_replay_equal
from benchmarks.extended_closed_loop_acceptance import NAMES,document,producer,compare,caps
from benchmarks.extended_closed_loop_reference import run as reference,channels
from power_grid_env.switching_drive import run_switching_drive
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


def assessment(p,coarse,medium,fine):
    keys=channels(p);limit=caps(p);groups={
        'network':[k for k in keys if k.startswith('source_current') or k=='dc_voltage_v'],
        'machine_observer':[k for k in keys if 'flux' in k or 'motor_current' in k or k=='speed_rad_s'],
        'thermal':[k for k in keys if 'temperature_c' in k]}
    out={}
    for name,selected in groups.items():
        indices=[keys.index(k) for k in selected];scale=np.array([limit[k] for k in selected])
        earlier=float(np.max(abs(np.asarray(coarse)[:,indices]-np.asarray(medium)[:,indices])/scale));later=float(np.max(abs(np.asarray(medium)[:,indices]-np.asarray(fine)[:,indices])/scale))
        # Absolute 1e-8 of the physical acceptance budget avoids ratios of
        # roundoff-only changes (e.g. an inactive brake temperature).
        out[name]=dict(coarse_medium_normalized=earlier,medium_fine_normalized=later,ratio=later/earlier if earlier>1e-8 else None,passed=later<=max(.85*earlier,1e-8))
    return dict(passed=all(x['passed'] for x in out.values()),groups=out)


def run(source,out,replay=False):
    source=Path(source);out=Path(out);identity=verify(source);before=dict(qualification=producer(),refinement_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),source_manifest_sha256=identity)
    if read_json(source/'producer.json')!=before['qualification'] or not read_json(source/'summary.json')['passed']:raise ValueError('source qualification not current and passing')
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('refinement producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            assert_replay_equal(read_json(out/name),value)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        p=dict(document(name),max_step_s=5e-6);a=run_switching_drive(p);b=reference(p);store(name+'-study.json',p);store(name+'-primary.json',a);store(name+'-reference.json',b)
        results={};keys=channels(p)
        for label,result in [('primary',a),('reference',b)]:
            values=[]
            for level in ['coarse','fine']:
                r=read_json(source/f'{name}-{level}-{label}.json');values.append(np.array([r['channels'][k] for k in keys]).T.tolist() if label=='primary' else r['rows'])
            values.append(np.array([result['channels'][k] for k in keys]).T.tolist() if label=='primary' else result['rows']);results[label]=assessment(p,*values)
        check=compare(p,a,b);row=dict(case=name,passed=check['passed'] and all(v['passed'] for v in results.values()),comparison=check,refinement=results);rows.append(row);print(encoded(row).decode(),flush=True)
    result=dict(schema='power-grid-env.extended-closed-loop-refinement/v1',passed=all(r['passed'] for r in rows),rows=rows,step_levels_s=[2e-5,1e-5,5e-6],additional_primary_trajectories=4,additional_reference_trajectories=4,scope='step reduction in three normalized physical groups; no asserted asymptotic order, continuous-time truth or external ranking',superiority_established=False)
    store('summary.json',result)
    if producer()!=before['qualification'] or verify(source)!=identity or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=before['refinement_sha256']:raise ValueError('refinement producer/source changed')
    receipt=verify(out) if replay else seal(out)
    if replay and receipt!=manifest:raise ValueError('refinement artifact changed')
    print(encoded(dict(passed=result['passed'],manifest_sha256=receipt,replayed=replay)).decode(),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');args=parser.parse_args();run(args.source,args.verify or args.out,bool(args.verify))

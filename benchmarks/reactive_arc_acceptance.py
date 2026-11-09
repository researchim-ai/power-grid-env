"""Offline Q-control branch qualification against pandapower's original runpp.

Reference solves start flat at every sampled load: no native voltage injection.
Independent restart control allows Q-limit release. Only registered packaged IEEE variants and fixed load scaling are qualified.
"""
import argparse,hashlib,json
from dataclasses import replace
from pathlib import Path
import numpy as np
from power_grid_env.ac_pseudo_arc import run_pseudo_arc,verify_pseudo_arc
from power_grid_env.balanced_ac import case_from_document,case_document,branch_results
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.ieee_ac_comparison import normalize_primary,independent_balance,compare_outputs
from benchmarks.reactive_arc_reference import solve_reference,normalize_control_reference

CASES=('case14','case30','case118')
STEPS=(.04,.02)
DATA=Path(__file__).resolve().parents[1]/'cases/ac-reactive-arc/reference-inputs'


def normalized(case,result):
    result=dict(result,iterations=0,branches=branch_results(case_from_document(case),np.array(result['vm_pu']),np.deg2rad(result['va_deg'])))
    return normalize_primary(case,result)


def score(case,primary,reference):
    comparison=compare_outputs({'case':case},{'result':primary},{'result':reference})
    qerror=max((abs(a[2]-b[2]) for a,b in zip(primary['generation_by_bus'],reference['generation_by_bus'],strict=True)),default=0.)
    balances=dict(primary=independent_balance(case,primary),reference=independent_balance(case,reference))
    return dict(passed=bool(comparison['passed'] and qerror<=1e-5 and max(balances.values())<=1e-6),
        comparison=comparison,maximum_bus_q_error_mvar=qerror,independent_balance_mva=balances)


def qualify(out):
    import pandapower as pp
    if pp.__version__!='3.5.6':raise ValueError('pandapower 3.5.6 required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();rows=[]
    for name in CASES:
        source=read_json(DATA/(name+'.json'));base=case_from_document(source['case'])
        target=replace(base,buses=[replace(b,pd=b.pd*1.2,qd=b.qd*1.2) for b in base.buses])
        source_file=out/(name+'-source.json');source_file.write_bytes(encoded(source))
        estimates=[]
        for step in STEPS:
            folder=out/name/str(step)
            document=dict(start=case_document(base),target=case_document(target),options=dict(enforce_q_limits=True,step=step,maximum_step=step,maximum_wall_s=120))
            r=run_pseudo_arc(document,folder);verify_pseudo_arc(folder)
            checks=[];references=[]
            samples=r['points']+([dict(lambda_value=1.,result=r['final_result'])] if r['completed'] else [])
            for point in samples:
                lam=point['lambda_value'];factor=1+.2*lam
                case=replace(base,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in base.buses]);cd=case_document(case)
                net,active,iterations=solve_reference(source,factor)
                reference=normalize_control_reference(cd,net,active,source);primary=normalized(cd,point['result'])
                references.append(reference);checks.append(score(cd,primary,reference))
            events=[dict(bus=e['bus_id'],before=e['before'],after=e['after'],
                lambda_mid=None if e['refinement']['right'] is None else (e['refinement']['left']['lambda_value']+e['refinement']['right']['lambda_value'])/2,
                localized=e['refinement']['localized']) for e in r['control_changes']]
            estimates.append(events)
            row=dict(case=name,step=step,completed=r['completed'],points=len(r['points']),events=events,
                maximum_voltage_error_pu=max((x['comparison']['maximum_errors']['voltage_pu'] for x in checks),default=None),
                maximum_bus_q_error_mvar=max((x['maximum_bus_q_error_mvar'] for x in checks),default=None),
                passed=bool(r['completed'] and all(x['passed'] for x in checks) and all(e['localized'] for e in events)))
            (folder.parent/(str(step)+'-references.json')).write_bytes(encoded(dict(references=references,checks=checks)))
            rows.append(row);print(json.dumps(row),flush=True)
        first,second=estimates
        inventory=lambda es:[(e['bus'],e['before'],e['after']) for e in es]
        agreement=inventory(first)==inventory(second) and all(a['lambda_mid'] is not None and b['lambda_mid'] is not None and abs(a['lambda_mid']-b['lambda_mid'])<=2e-6 for a,b in zip(first,second))
        rows.append(dict(case=name,step_refinement=True,event_lambda_tolerance=2e-6,passed=agreement))
    summary=dict(schema='pge-reactive-arc-qualification/v1',producer_sha256=code,benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        passed=all(x['passed'] for x in rows) and package_fingerprint()==code,rows=rows,reference_controller_sha256=hashlib.sha256(Path(__file__).with_name('reactive_arc_reference.py').read_bytes()).hexdigest(),reference='pandapower 3.5.6 original runpp equations with independent bounded active-set restart and limit release; flat start for every trial',
        reference_continuation_executed=False,matpower_executed=False,slack_q_enforced=False,exhaustive_event_detection=False,stability_proven=False)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);summary=read_json(out/'summary.json');rows=[]
    if summary['producer_sha256']!=package_fingerprint() or summary['benchmark_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or summary['reference_controller_sha256']!=hashlib.sha256(Path(__file__).with_name('reactive_arc_reference.py').read_bytes()).hexdigest():raise ValueError('current producers required')
    for name in CASES:
        source=read_json(out/(name+'-source.json'))
        if source!=read_json(DATA/(name+'.json')):raise ValueError('frozen registered source differs')
        base=case_from_document(source['case'])
        estimates=[]
        for step in STEPS:
            folder=out/name/str(step);verify_pseudo_arc(folder);d=read_json(folder/'input.json');r=read_json(folder/'result.json')
            target=replace(base,buses=[replace(b,pd=b.pd*1.2,qd=b.qd*1.2) for b in base.buses])
            if encoded(d)!=encoded(dict(start=case_document(base),target=case_document(target),options=dict(enforce_q_limits=True,step=step,maximum_step=step,maximum_wall_s=120))):raise ValueError('registered branch input differs')
            reference=read_json(folder.parent/(str(step)+'-references.json'));checks=[]
            samples=r['points']+([dict(lambda_value=1.,result=r['final_result'])] if r['completed'] else [])
            if len(reference['references'])!=len(samples):raise ValueError('reference inventory differs')
            for point,ref in zip(samples,reference['references'],strict=True):
                factor=1+.2*point['lambda_value'];case=case_document(replace(base,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in base.buses]))
                checks.append(score(case,normalized(case,point['result']),ref))
            if checks!=reference['checks']:raise ValueError('reference comparison arithmetic differs')
            events=[dict(bus=e['bus_id'],before=e['before'],after=e['after'],lambda_mid=None if e['refinement']['right'] is None else (e['refinement']['left']['lambda_value']+e['refinement']['right']['lambda_value'])/2,localized=e['refinement']['localized']) for e in r['control_changes']]
            estimates.append(events)
            rows.append(dict(case=name,step=step,completed=r['completed'],points=len(r['points']),events=events,
                maximum_voltage_error_pu=max((x['comparison']['maximum_errors']['voltage_pu'] for x in checks),default=None),maximum_bus_q_error_mvar=max((x['maximum_bus_q_error_mvar'] for x in checks),default=None),
                passed=bool(r['completed'] and all(x['passed'] for x in checks) and all(e['localized'] for e in events))))
        first,second=estimates
        same=[(e['bus'],e['before'],e['after']) for e in first]==[(e['bus'],e['before'],e['after']) for e in second]
        agrees=same and all(a['lambda_mid'] is not None and b['lambda_mid'] is not None and abs(a['lambda_mid']-b['lambda_mid'])<=2e-6 for a,b in zip(first,second))
        rows.append(dict(case=name,step_refinement=True,event_lambda_tolerance=2e-6,passed=agrees))
    if summary['rows']!=rows or summary['passed']!=all(x['passed'] for x in rows):raise ValueError('qualification conclusion differs')
    if any(summary[k] for k in ['reference_continuation_executed','matpower_executed','slack_q_enforced','exhaustive_event_detection','stability_proven']):raise ValueError('unsupported qualification claim')
    return dict(verified=True,passed=summary['passed'],manifest_sha256=identity,scope='recomputed stored electrical balances, projected control branches and reference comparisons; no reference solver replay')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args()
    r=qualify(a.out) if a.out else verify_qualification(a.verify)
    print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

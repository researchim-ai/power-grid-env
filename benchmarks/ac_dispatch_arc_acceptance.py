"""Offline finite joint distributed continuation/envelope qualification."""
import argparse,hashlib,json
from pathlib import Path
from dataclasses import replace
import numpy as np
from power_grid_env.ac_envelope import run_envelope,verify_envelope
from power_grid_env.ac_pseudo_arc import make_family,PseudoArcOptions
from power_grid_env.balanced_ac import case_from_document,case_document,branch_results
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.ac_dispatch_reference import solve_reference
from benchmarks.ac_dispatch_acceptance import score
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'cases/ac-dispatch-arc';STEPS=(.04,.02)


def inputs():return sorted(p for p in DATA.glob('*.json') if p.name!='origin.json')


def source_hashes():
    files=[Path(__file__),Path(__file__).with_name('ac_dispatch_reference.py'),Path(__file__).with_name('ac_dispatch_acceptance.py'),*inputs()]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def states(study):
    r=study['trace'];m=study['envelope'];out=[]
    for i,p in enumerate(r['points']):out.append((f'point:{i}',p['lambda_value'],p['result']))
    for kind,events in [('q',r['control_changes']),('p',r['p_control_changes'])]:
        for i,event in enumerate(events):
            for side in ['left','right']:
                p=event['refinement'][side]
                if p is not None:out.append((f'{kind}:{i}:{side}',p['lambda_value'],p['result']))
    for i,event in enumerate(m['events']):
        for side in ['left','right']:
            p=event[side];out.append((f'limit:{i}:{side}',p['lambda_value'],p['result']))
    if r['completed']:out.append(('target',1.,r['final_result']))
    return out


def compare(family,lam,state,ref):
    case=family.load_case(lam);d=case_document(case)
    r=dict(state,branches=branch_results(case,np.array(state['vm_pu']),np.deg2rad(state['va_deg'])))
    c=score(d,r,ref);c['delta_error_mw']=abs(state['delta_mw']-ref['result']['delta_mw']);c['passed']=bool(c['passed'] and c['delta_error_mw']<=1e-5)
    return c


def event_estimates(study):
    out=[]
    for kind,events,key in [('p',study['trace']['p_control_changes'],'generator_index'),('q',study['trace']['control_changes'],'bus_id')]:
        for e in events:
            b=e['refinement'];right=b['right'];out.append(dict(kind=kind,equipment=e[key],before=e['before'],after=e['after'],localized=b['localized'],lambda_mid=None if right is None else (b['left']['lambda_value']+right['lambda_value'])/2))
    for e in study['envelope']['events']:
        out.append(dict(kind='limit',equipment=e['key'],before='inside' if e['direction']=='exit' else 'outside',after='outside' if e['direction']=='exit' else 'inside',localized=e['localized'],lambda_mid=(e['left']['lambda_value']+e['right']['lambda_value'])/2))
    return out


def row(name,step,study,checks):
    events=event_estimates(study)
    return dict(case=name,step=step,states=len(checks),events=events,target_within_monitored_limits=study['envelope']['target_within_monitored_limits'],
        max_voltage_error_pu=max((x['errors']['voltage_pu'] for x in checks),default=0.),max_pg_error_mw=max((x['errors']['pg_mw'] for x in checks),default=0.),
        max_delta_error_mw=max((x['delta_error_mw'] for x in checks),default=0.),passed=bool(study['trace']['completed'] and study['envelope']['completed'] and all(e['localized'] for e in events) and all(x['passed'] for x in checks)))


def step_comparison(name,rows):
    def keyed(events):
        result={};counts={}
        for e in events:
            key=(e['kind'],str(e['equipment']),e['before'],e['after']);counts[key]=counts.get(key,0)+1;result[(*key,counts[key])]=e
        return result
    a,b=[keyed(r['events']) for r in rows]
    error=None if a.keys()!=b.keys() or any(e['lambda_mid'] is None for e in [*a.values(),*b.values()]) else max((abs(a[k]['lambda_mid']-b[k]['lambda_mid']) for k in a),default=0.)
    return dict(case=name,step_refinement=True,maximum_event_lambda_difference=error,passed=error is not None and error<=2e-6)


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();hashes=source_hashes();rows=[]
    for path in inputs():
        levels=[];cache={}
        for step in STEPS:
            d=read_json(path);d['options'].update(step=step,maximum_step=step)
            folder=out/path.stem/str(step);study=run_envelope(d,folder);verify_envelope(folder)
            o=PseudoArcOptions.model_validate(d['options']);f=make_family(case_from_document(d['start']),case_from_document(d['target']),o);references=[];checks=[]
            for label,lam,state in states(study):
                if lam not in cache:cache[lam]=solve_reference(f.load_case(lam),o.dispatch.model_dump(mode='json'))
                ref=cache[lam];comparison=compare(f,lam,state,ref);checks.append(comparison);references.append(dict(label=label,reference=ref,comparison=comparison))
            (folder.parent/(str(step)+'-references.json')).write_bytes(encoded(references))
            result=row(path.stem,step,study,checks);rows.append(result);levels.append(result);print(json.dumps(result),flush=True)
        rows.append(step_comparison(path.stem,levels))
    s=dict(schema='pge-dispatch-arc-qualification/v1',producer_sha256=code,source_hashes=hashes,rows=rows,passed=all(r['passed'] for r in rows) and package_fingerprint()==code and source_hashes()==hashes,
        reference='PP 3.5.6 makeYbus/Yf/Yt + independent residual, SciPy numerical-Jacobian hybr; cold seed at every sampled lambda',reference_continuation_executed=False,exhaustive_event_detection=False,stability_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=source_hashes():raise ValueError('current joint-study producers/registered inputs required')
    for path in inputs():
        levels=[]
        for step in STEPS:
            folder=out/path.stem/str(step);verify_envelope(folder);d=read_json(folder/'input.json');expected=read_json(path);expected['options'].update(step=step,maximum_step=step)
            if d!=expected:raise ValueError('registered joint input differs')
            study=dict(trace=read_json(folder/'trace.json'),envelope=read_json(folder/'envelope.json'));o=PseudoArcOptions.model_validate(d['options']);f=make_family(case_from_document(d['start']),case_from_document(d['target']),o)
            refs=read_json(folder.parent/(str(step)+'-references.json'));sampled=states(study);checks=[]
            if len(refs)!=len(sampled):raise ValueError('reference state inventory differs')
            for (label,lam,state),ref in zip(sampled,refs,strict=True):
                c=compare(f,lam,state,ref['reference'])
                if ref['label']!=label or ref['comparison']!=c:raise ValueError('joint comparison differs')
                checks.append(c)
            result=row(path.stem,step,study,checks);rows.append(result);levels.append(result)
        rows.append(step_comparison(path.stem,levels))
    if s['rows']!=rows or s['passed']!=all(r['passed'] for r in rows):raise ValueError('joint qualification conclusion differs')
    if any(s[k] for k in ['reference_continuation_executed','exhaustive_event_detection','stability_proven']):raise ValueError('unsupported joint study claim')
    return dict(verified=True,passed=s['passed'],states=sum(r.get('states',0) for r in rows),manifest_sha256=identity,scope='stored distributed branches, event brackets and independent terminal comparisons; no solver replay or authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

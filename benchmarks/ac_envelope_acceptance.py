"""Finite offline AC operating-envelope qualification; no universal capacity claim."""
import argparse,hashlib,json
from pathlib import Path
from dataclasses import replace
import numpy as np
from power_grid_env.ac_envelope import run_envelope,verify_envelope,measure,limits_for,EnvelopeOptions
from power_grid_env.balanced_ac import case_document,case_from_document,branch_results
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.reactive_arc_reference import solve_reference,normalize_control_reference
from benchmarks.ieee_ac_comparison import normalize_primary,independent_balance
ROOT=Path(__file__).resolve().parents[1]
CASES=('case14','case30','case118')
STEPS=(.04,.02)


def reference_values(case,ref,limits):
    """Infer terminal current from original PP terminal S and Vm, not own stamps."""
    idx={b['id']:i for i,b in enumerate(case['buses'])};out={}
    for c in limits:
        if c['kind']=='voltage':value=ref['voltage_pu'][c['bus_index']]
        else:
            b=case['branches'][c['branch_index']];s=ref['branch_power_mva'][c['branch_index']];side=0 if c['terminal']=='from' else 2
            value=float(np.hypot(s[side],s[side+1]))
            if c['kind']=='current':value/=case['base_mva']*ref['voltage_pu'][idx[b['fbus'] if side==0 else b['tbus']]]
        margin=value-c['threshold'] if c.get('side')=='lower' else c['threshold']-value
        if c['kind']=='mva':margin/=case['base_mva']
        out[c['key']]=dict(value=float(value),margin_pu=float(margin))
    return out


def samples(study):
    trace=study['trace'];report=study['envelope'];result=[]
    for i,p in enumerate(trace['points']):result.append((f'point:{i}',p['lambda_value'],p['result']))
    for i,e in enumerate(report['events']):
        for side in ['left','right']:
            p=e[side];result.append((f'event:{i}:{side}',p['lambda_value'],p['result']))
    if trace['completed']:result.append(('target',1.,trace['final_result']))
    return result


def score(document,lam,state,ref):
    start=case_from_document(document['start']);target=case_from_document(document['target'])
    case=replace(start,buses=[replace(a,pd=a.pd+lam*(b.pd-a.pd),qd=a.qd+lam*(b.qd-a.qd)) for a,b in zip(start.buses,target.buses,strict=True)])
    cd=case_document(case);limits=limits_for(start,EnvelopeOptions.model_validate(document['monitor']))
    measured=measure(case,state,limits);expected=reference_values(cd,ref,limits)
    state=dict(state,iterations=0,branches=branch_results(case,np.array(state['vm_pu']),np.deg2rad(state['va_deg'])))
    primary=normalize_primary(cd,state)
    errors={key:float(np.max(abs(np.array(primary[key])-np.array(ref[key])),initial=0.)) for key in ['voltage_pu','angle_rad','branch_power_mva']}
    balances=dict(primary=independent_balance(cd,primary),reference=independent_balance(cd,ref))
    margin_error=max((abs(measured[k]['margin_pu']-expected[k]['margin_pu']) for k in measured),default=0.)
    unit_errors={unit:max((abs(measured[c['key']]['value']-expected[c['key']]['value']) for c in limits if c['unit']==unit),default=0.) for unit in ['pu','MVA']}
    passed=errors['voltage_pu']<=1e-8 and errors['angle_rad']<=1e-8 and errors['branch_power_mva']<=1e-5 and max(balances.values())<=1e-6 and margin_error<=1e-8 and unit_errors['pu']<=1e-8 and unit_errors['MVA']<=1e-5
    return dict(passed=bool(passed),errors=errors,independent_balance_mva=balances,maximum_margin_error_pu=margin_error,maximum_measurement_errors=unit_errors)


def row(name,step,study,checks):
    events=[dict(key=e['key'],direction=e['direction'],lambda_mid=(e['left']['lambda_value']+e['right']['lambda_value'])/2,localized=e['localized']) for e in study['envelope']['events']]
    return dict(case=name,step=step,states=len(checks),events=events,target_within_monitored_limits=study['envelope']['target_within_monitored_limits'],
        max_voltage_error_pu=max((x['errors']['voltage_pu'] for x in checks),default=0.),max_margin_error_pu=max((x['maximum_margin_error_pu'] for x in checks),default=0.),
        passed=bool(study['trace']['completed'] and study['envelope']['completed'] and all(x['passed'] for x in checks)))


def step_comparison(name,levels):
    def keyed(events):
        counts={};out={}
        for e in events:
            key=(e['key'],e['direction']);counts[key]=counts.get(key,0)+1;out[(*key,counts[key])]=e
        return out
    a,b=[keyed(x['events']) for x in levels]
    error=None if a.keys()!=b.keys() else max((abs(a[k]['lambda_mid']-b[k]['lambda_mid']) for k in a),default=0.)
    return dict(case=name,step_refinement=True,maximum_event_lambda_difference=error,passed=error is not None and error<=2e-6)


def source_hashes():
    paths=[Path(__file__),Path(__file__).with_name('reactive_arc_reference.py')]
    paths += [ROOT/'cases/ac-envelope'/(name+'.json') for name in CASES]
    paths += [ROOT/'cases/ac-reactive-arc/reference-inputs'/(name+'.json') for name in CASES]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();hashes=source_hashes();rows=[]
    for name in CASES:
        source=read_json(ROOT/'cases/ac-reactive-arc/reference-inputs'/(name+'.json'));levels=[];cache={}
        for step in STEPS:
            d=read_json(ROOT/'cases/ac-envelope'/(name+'.json'));d['options'].update(step=step,maximum_step=step)
            path=out/name/str(step);study=run_envelope(d,path);verify_envelope(path);checks=[];refs=[]
            for label,lam,state in samples(study):
                if lam not in cache:
                    net,active,_=solve_reference(source,1+.2*lam)
                    cache[lam]=normalize_control_reference(d['start'],net,active,source)
                ref=cache[lam];s=score(d,lam,state,ref);refs.append(dict(label=label,reference=ref,comparison=s));checks.append(s)
            (path.parent/(str(step)+'-references.json')).write_bytes(encoded(refs))
            result=row(name,step,study,checks);levels.append(result);rows.append(result);print(json.dumps(result),flush=True)
        rows.append(step_comparison(name,levels))
    summary=dict(schema='power-grid-env.ac-envelope-qualification/v1',producer_sha256=code,source_hashes=hashes,rows=rows,
        passed=all(x['passed'] for x in rows) and package_fingerprint()==code and source_hashes()==hashes,
        reference='pandapower 3.5.6 original runpp with separate active-set release controller; flat starts; terminal current inferred from PP S and V',
        exhaustive_event_detection=False,protection_actions_simulated=False,matpower_executed=False,stability_proven=False)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=source_hashes():raise ValueError('current qualification producers and registered inputs required')
    for name in CASES:
        levels=[]
        for step in STEPS:
            path=out/name/str(step);verify_envelope(path);d=read_json(path/'input.json');expected=read_json(ROOT/'cases/ac-envelope'/(name+'.json'));expected['options'].update(step=step,maximum_step=step)
            if d!=expected:raise ValueError('registered envelope input differs')
            study=dict(trace=read_json(path/'trace.json'),envelope=read_json(path/'envelope.json'));refs=read_json(path.parent/(str(step)+'-references.json'));states=samples(study)
            if len(refs)!=len(states):raise ValueError('reference state inventory differs')
            checks=[]
            for (label,lam,state),ref in zip(states,refs,strict=True):
                check=score(d,lam,state,ref['reference'])
                if ref['label']!=label or ref['comparison']!=check:raise ValueError('reference comparison differs')
                checks.append(check)
            result=row(name,step,study,checks);levels.append(result);rows.append(result)
        rows.append(step_comparison(name,levels))
    if s['rows']!=rows or s['passed']!=all(x['passed'] for x in rows):raise ValueError('qualification conclusion differs')
    if any(s[k] for k in ['exhaustive_event_detection','protection_actions_simulated','matpower_executed','stability_proven']):raise ValueError('unsupported qualification claim')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,states=sum(x.get('states',0) for x in rows),scope='stored state/terminal measurements, event intervals and independent comparisons; no Newton replay or source authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args()
    r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

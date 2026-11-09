"""Bounded adaptive load homotopy; no collapse or branch-stability proof.

Only bus Pd/Qd change. Failed attempts roll back to the last independently
balance-audited accepted state. A final operating point is always solved at
exactly the target loads. This is not pseudo-arclength continuation power flow.
"""
from dataclasses import replace
import math
import time
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
import numpy as np
from power_grid_env.balanced_ac import case_from_document,case_document,solve_power_flow
from power_grid_env.network_opf import audit_feasibility
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint


class ContinuationOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    initial_step:float=Field(default=.1,gt=0,le=1)
    minimum_step:float=Field(default=1/4096,gt=0,le=.1)
    maximum_attempts:int=Field(default=96,ge=1,le=256)
    maximum_wall_s:float=Field(default=60,gt=0,le=600)
    tolerance_pu:float=Field(default=1e-10,gt=0,le=1e-6)
    max_iterations:int=Field(default=40,ge=1,le=200)
    enforce_q_limits:bool=False


def compatible(start,target):
    a=case_document(start);b=case_document(target)
    if any(x.type_code in (2,3) and x.vm!=y.vm for x,y in zip(start.buses,target.buses,strict=True)):raise ValueError('controlled voltage setpoints must not change')
    for d in [a,b]:
        d.pop('name');d.pop('published_cost',None)
        for bus in d['buses']:
            for key in ['pd','qd','vm','va_deg']:bus.pop(key)
    if a!=b:raise ValueError('load continuation requires identical topology, bases, equipment, schedules and limits; only bus Pd/Qd may change')


def point(start,target,progress,seed=None):
    if not math.isfinite(progress) or not 0<=progress<=1:raise ValueError('progress must be finite in [0,1]')
    buses=[]
    for i,(a,b) in enumerate(zip(start.buses,target.buses,strict=True)):
        fields=dict(pd=b.pd if progress==1. else a.pd+(b.pd-a.pd)*progress,
            qd=b.qd if progress==1. else a.qd+(b.qd-a.qd)*progress)
        if seed is not None:fields.update(vm=a.vm if a.type_code in (2,3) else seed['vm_pu'][i],va_deg=seed['va_deg'][i])
        buses.append(replace(a,**fields))
    return replace(target,buses=buses)


def physical_audit(case,result,options):
    if result.get('completed') is not True:return None
    if result.get('bus_ids')!=[b.id for b in case.buses]:raise ValueError('continuation bus identity/order mismatch')
    audit=audit_feasibility(case,np.asarray(result['vm_pu']),np.deg2rad(result['va_deg']),np.asarray(result['pg_mw']),np.asarray(result['qg_mvar']),
        tolerance=max(options.tolerance_pu*10,1e-8),matrix_format='sparse')
    if audit['max_mismatch_pu'] is None:raise ValueError('nonfinite continuation operating point')
    vm=np.asarray(result['vm_pu']);pg=np.asarray(result['pg_mw']);qg=np.asarray(result['qg_mvar'])
    tolerance=max(options.tolerance_pu*case.base_mva*10,1e-6)
    buses={b.id:b for b in case.buses};scheduled=True;voltages=True
    for i,g in enumerate(case.generators):
        b=buses[g.bus]
        if b.type_code!=3:scheduled=scheduled and g.pg is not None and abs(pg[i]-g.pg)<=tolerance
        if b.type_code==1:scheduled=scheduled and abs(qg[i]-(g.qg or 0.))<=tolerance
    for i,b in enumerate(case.buses):
        if b.type_code not in (2,3):continue
        setpoint=abs(vm[i]-b.vm)<=1e-8
        if b.type_code==3 or not options.enforce_q_limits:voltages=voltages and setpoint
        else:
            units=[k for k,g in enumerate(case.generators) if g.bus==b.id]
            q=sum(qg[k] for k in units);lo=sum(case.generators[k].qmin for k in units);hi=sum(case.generators[k].qmax for k in units)
            voltages=voltages and lo-tolerance<=q<=hi+tolerance and (setpoint or min(abs(q-lo),abs(q-hi))<=tolerance)
    return dict(balance_acceptable=bool(scheduled and voltages and np.all((vm>.02)&(vm<3)) and audit['max_mismatch_pu']<=max(options.tolerance_pu*10,1e-8)),
        schedules_acceptable=bool(scheduled),controlled_voltages_acceptable=bool(voltages),
        maximum_mismatch_pu=audit['max_mismatch_pu'],within_all_reported_limits=audit['feasible'])


def snapshot(result):
    keys=['completed','status','bus_ids','vm_pu','va_deg','pg_mw','qg_mvar','iterations']
    if 'delta_mw' in result:keys+=['delta_mw','participation','p_modes','q_modes','angle_reference_bus']
    return {k:result[k] for k in keys if k in result}


def continue_power_flow(start,target,options=None,*,cancelled=None,solver=None):
    options=ContinuationOptions.model_validate(options or {})
    options=ContinuationOptions.model_validate(options.model_dump(mode='json'))
    start=case_from_document(case_document(start));target=case_from_document(case_document(target))
    if options.minimum_step>options.initial_step:raise ValueError('minimum step exceeds initial step')
    compatible(start,target);started=time.monotonic();code=package_fingerprint()
    callback=solver is not None
    if solver is None:
        def solver(case,progress):
            return solve_power_flow(case,linear_solver='sparse',tolerance_pu=options.tolerance_pu,max_iterations=options.max_iterations,enforce_q_limits=options.enforce_q_limits)
    accepted=None;progress=0.;step=options.initial_step;attempts=[];reason='maximum_attempts';final=None
    for _ in range(options.maximum_attempts):
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-started>=options.maximum_wall_s:reason='wall_deadline';break
        if package_fingerprint()!=code:reason='producer_changed';break
        candidate_progress=0. if accepted is None else min(1.,progress+step)
        candidate=point(start,target,candidate_progress,accepted)
        result=solver(candidate,candidate_progress);audit=physical_audit(candidate,result,options)
        ok=bool(audit and audit['balance_acceptable'])
        discard='producer_changed' if package_fingerprint()!=code else 'wall_deadline' if time.monotonic()-started>=options.maximum_wall_s else None
        attempts.append(dict(discard_reason=discard,progress=candidate_progress,previous_accepted_progress=progress if accepted is not None else None,
            step=0. if accepted is None else candidate_progress-progress,accepted=bool(ok and discard is None),status=result.get('status','unknown'),
            audit=audit,result=snapshot(result)))
        if discard is not None:reason=discard;break
        if ok:
            accepted=result;progress=candidate_progress
            if progress==1.:final=result;reason=None;break
            step=min(1.-progress,step*(1.5 if progress>0 else 1.))
        else:
            if accepted is None:reason='base_not_converged';break
            step/=2
            if step<options.minimum_step:reason='minimum_step';break
    completed=final is not None
    return dict(schema='power-grid-env.ac-continuation-result/v1',completed=completed,status='target_reached' if completed else 'incomplete',reason=reason,
        producer_sha256=code,accepted_progress=progress,attempts=attempts,final_result=final,
        options=options.model_dump(mode='json'),callback_solver=callback,elapsed_s=time.monotonic()-started,
        target_loads_exact=completed,voltage_collapse_proven=False,stability_proven=False,
        scope='adaptive load interpolation with repeated Newton solves and independent balance rebuild; not pseudo-arclength CPF or proof no solution exists')


def run_continuation(document,out):
    if set(document)-{'start','target','options','provenance'}:raise ValueError('unknown continuation input fields')
    start=case_from_document(document['start']);target=case_from_document(document['target']);options=ContinuationOptions.model_validate(document.get('options',{}))
    compatible(start,target);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    payload=dict(start=case_document(start),target=case_document(target),options=options.model_dump(mode='json'))
    if 'provenance' in document:
        provenance=document['provenance']
        if not isinstance(provenance,dict) or len(provenance)>16 or any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip() or len(k)>128 or len(v)>4096 for k,v in provenance.items()):raise ValueError('bounded nonblank provenance strings required')
        payload['provenance']=provenance
    if len(encoded(payload))>16000000:raise ValueError('continuation input budget')
    (out/'input.json').write_bytes(encoded(payload));result=continue_power_flow(start,target,options.model_dump(mode='json'))
    raw=encoded(result)
    if len(raw)>64000000:raise ValueError('continuation result budget')
    (out/'result.json').write_bytes(raw);seal(out);return result


def verify_continuation(directory):
    directory=Path(directory);identity=verify(directory);document=read_json(directory/'input.json');r=read_json(directory/'result.json')
    start=case_from_document(document['start']);target=case_from_document(document['target']);compatible(start,target)
    options=ContinuationOptions.model_validate(document['options'])
    if r['producer_sha256']!=package_fingerprint() or r['options']!=options.model_dump(mode='json') or r['callback_solver']:raise ValueError('current native continuation producer/options required')
    if len(r['attempts'])>options.maximum_attempts:raise ValueError('continuation attempt budget differs')
    accepted=None;progress=0.
    for index,attempt in enumerate(r['attempts']):
        value=attempt['progress']
        if not math.isfinite(value) or not 0<=value<=1 or (accepted is not None and value<=progress) or (accepted is None and value!=0):raise ValueError('invalid continuation path')
        if attempt['step']!=(0. if accepted is None else value-progress):raise ValueError('continuation step differs')
        if attempt['previous_accepted_progress']!=(None if accepted is None else progress):raise ValueError('rollback anchor differs')
        candidate=point(start,target,value,accepted);audit=physical_audit(candidate,attempt['result'],options)
        discard=attempt.get('discard_reason')
        if discard is not None and (discard not in {'producer_changed','wall_deadline'} or r['reason']!=discard or index!=len(r['attempts'])-1):raise ValueError('invalid discarded step')
        ok=bool(audit and audit['balance_acceptable'] and discard is None)
        if audit!=attempt['audit'] or ok!=attempt['accepted']:raise ValueError('accepted step differs from recomputed balance')
        if ok:accepted=attempt['result'];progress=value
    if not r['completed'] and r['reason'] not in {'maximum_attempts','minimum_step','base_not_converged','cancelled','wall_deadline','producer_changed'}:raise ValueError('unknown incomplete reason')
    if r['reason']=='maximum_attempts' and len(r['attempts'])!=options.maximum_attempts:raise ValueError('attempt exhaustion differs')
    if r['reason']=='minimum_step' and (not r['attempts'] or r['attempts'][-1]['accepted'] or r['attempts'][-1]['step']/2>=options.minimum_step):raise ValueError('minimum-step stop differs')
    if r['status']!=('target_reached' if r['completed'] else 'incomplete') or (r['completed'] and r['reason'] is not None):raise ValueError('continuation status differs')
    if r['accepted_progress']!=progress or r['completed']!=(progress==1.) or r['target_loads_exact']!=r['completed'] or r['voltage_collapse_proven'] or r['stability_proven']:raise ValueError('continuation conclusion differs')
    if r['completed']:
        if snapshot(r['final_result'])!=accepted or not physical_audit(point(start,target,1.),r['final_result'],options)['balance_acceptable']:raise ValueError('final target differs')
    elif r['final_result'] is not None:raise ValueError('incomplete path cannot claim a final target')
    return dict(verified=True,manifest_sha256=identity,completed=r['completed'],accepted_progress=progress,
        scope='recomputed stored step balances/rollback anchors; Newton and elapsed time not replayed; no source authentication')

"""Matched load-continuation controller on retained RTE cases.

The controller is shared; Newton backends and their evolving states are
separate. Terminal-current power balance is independently recomputed at every
accepted step. DC seed remains shared frozen input, prepared outside this run.
"""
import argparse
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import numpy as np
from power_grid_env.balanced_ac import case_from_document,case_document
from power_grid_env.ac_continuation import continue_power_flow,run_continuation,verify_continuation,point
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.ieee_ac_comparison import normalize_primary,normalize_reference,compare_outputs,independent_balance

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'cases/ac-continuation/reference-inputs'
GOALS=(.95,1.05,.9,1.1)


def normalized_state(case,r):
    units={b.id:[0.,0.] for b in case.buses}
    for g,p,q in zip(case.generators,r['pg_mw'],r['qg_mvar'],strict=True):units[g.bus][0]+=p;units[g.bus][1]+=q
    return dict(voltage_pu=r['vm_pu'],angle_rad=np.deg2rad(r['va_deg']).tolist(),generation_by_bus=[[k,*v] for k,v in units.items()])


def audit_path(start,target,r):
    errors=[]
    for step in r['attempts']:
        if step['accepted']:
            case=point(start,target,step['progress']);errors.append(independent_balance(case_document(case),normalized_state(case,step['result'])))
    return dict(passed=bool(errors) and all(e<=1e-6 for e in errors),accepted_steps=len(errors),maximum_independent_balance_mva=max(errors,default=None))


def reference_solver(base_document,factor):
    import pandapower as pp
    from pandapower.auxiliary import LoadflowNotConverged
    if pp.__version__!='3.5.6':raise ValueError('pinned pandapower required')
    original=pp.from_json_string(base_document['pandapower_json'])
    def solve(case,progress):
        net=copy.deepcopy(original);load_factor=1.+(factor-1.)*progress
        net.load['p_mw']*=load_factor;net.load['q_mvar']*=load_factor
        net.res_bus['vm_pu']=[b.vm for b in case.buses];net.res_bus['va_degree']=[b.va_deg for b in case.buses]
        try:pp.runpp(net,algorithm='nr',init='results',trafo_model='pi',numba=True,lightsim2grid=False,tolerance_mva=1e-10,max_iteration=40,
            enforce_q_lims=False,voltage_depend_loads=False,distributed_slack=False)
        except LoadflowNotConverged:return dict(completed=False,status='reference_not_converged',iterations=int(net._ppc.get('iterations',0)))
        if [int(g[0]) for g in net._ppc['gen']]!=[g.bus for g in case.generators]:raise ValueError('reference generator inventory differs')
        physical=normalize_reference(case_document(case),net)
        return dict(completed=True,status='converged',bus_ids=[b.id for b in case.buses],vm_pu=physical['voltage_pu'],va_deg=np.rad2deg(physical['angle_rad']).tolist(),
            pg_mw=net._ppc['gen'][:,1].tolist(),qg_mvar=net._ppc['gen'][:,2].tolist(),iterations=int(net._ppc['iterations']),normalized=physical)
    return solve



def case_row(start,target,factor,native,reference):
    native_audit=audit_path(start,target,native);reference_audit=audit_path(start,target,reference);match=None
    if native['completed'] and reference['completed']:
        a=normalize_primary(case_document(target),native['final_result']);b=reference['final_result']['normalized']
        match=compare_outputs(dict(case=case_document(target)),dict(result=a),dict(result=b))
    row=dict(load_factor=factor,primary_completed=native['completed'],reference_completed=reference['completed'],
        primary_progress=native['accepted_progress'],reference_progress=reference['accepted_progress'],
        primary_reason=native['reason'],reference_reason=reference['reason'],primary_path=native_audit,reference_path=reference_audit,
        target_comparison=match,voltage_collapse_proven=False)
    row['target_qualified']=bool(native['completed'] and reference['completed'] and native_audit['passed'] and reference_audit['passed'] and match['passed'])
    return row


def run(out):
    import pandapower as pp
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint()
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['power_grid_env','benchmarks'] for p in (ROOT/folder).glob('*.py')}
    pp_root=Path(pp.__file__).parent;ref_sources={str(p.relative_to(pp_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in pp_root.rglob('*.py')}
    def write(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(value))
    origin=read_json(SOURCE/'origin.json')
    if any(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()!=value for name,value in origin['files'].items()):raise ValueError('frozen continuation input identity differs')
    base=read_json(SOURCE/'rte-100.json');start=case_from_document(base['case'])
    endpoints={f:read_json(SOURCE/('rte-'+str(round(f*100))+'.json')) for f in [.9,1.1]}
    write('protocol.json',dict(goals=list(GOALS),options=dict(initial_step=.1,minimum_step=1/4096,maximum_attempts=96,maximum_wall_s=60,tolerance_pu=1e-10,max_iterations=40,enforce_q_limits=False),
        producer_sha256=code,source_sha256=sources,reference_sources=ref_sources,shared_controller=True,independent_newton_backends=True,shared_frozen_dc_seed=True,
        frozen_input_origin=read_json(SOURCE/'origin.json'),scope='one workflow run per backend/goal, no throughput ranking or full initializer qualification'))
    write('base.json',base)
    for f,d in endpoints.items():write('endpoint-'+str(round(f*100))+'.json',d)
    rows=[]
    for factor in GOALS:
        extreme=.9 if factor<1 else 1.1;full_target=case_from_document(endpoints[extreme]['case'])
        fraction=(factor-1)/(extreme-1);target=point(start,full_target,fraction);target=replace(target,name='rte-continuation-'+str(round(factor*100)))
        folder=str(round(factor*100));document=dict(start=case_document(start),target=case_document(target))
        native=run_continuation(document,out/folder/'primary');verify_continuation(out/folder/'primary')
        reference=continue_power_flow(start,target,solver=reference_solver(base,factor));write(folder+'/reference.json',reference)
        row=case_row(start,target,factor,native,reference)
        write(folder+'/comparison.json',row);rows.append(row)
    unchanged=code==package_fingerprint() and all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in sources.items()) and all(hashlib.sha256((pp_root/k).read_bytes()).hexdigest()==v for k,v in ref_sources.items())
    result=dict(producer_sha256=code,source_unchanged=unchanged,cases=rows,all_targets_qualified=unchanged and all(r['target_qualified'] for r in rows),
        original_90_110_points_closed=unchanged and all(r['target_qualified'] for r in rows if r['load_factor'] in [.9,1.1]),
        independent_controller=False,independent_terminal_balance=True,stability_proven=False,voltage_collapse_proven=False,superiority_proven=False)
    write('summary.json',result);seal(out);return result


def verify_result(directory):
    from power_grid_env.ac_continuation import physical_audit,ContinuationOptions
    directory=Path(directory);identity=verify(directory);p=read_json(directory/'protocol.json');r=read_json(directory/'summary.json')
    if p['producer_sha256']!=package_fingerprint() or p['goals']!=list(GOALS):raise ValueError('current registered continuation corpus required')
    if p['source_sha256']['benchmarks/ac_continuation_acceptance.py']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('benchmark source differs')
    base=read_json(directory/'base.json');start=case_from_document(base['case']);options=ContinuationOptions.model_validate(p['options']);rows=[]
    for factor in GOALS:
        folder=directory/str(round(factor*100));extreme=.9 if factor<1 else 1.1
        full=case_from_document(read_json(directory/('endpoint-'+str(round(extreme*100))+'.json'))['case'])
        target=replace(point(start,full,(factor-1)/(extreme-1)),name='rte-continuation-'+str(round(factor*100)))
        document=read_json(folder/'primary/input.json')
        if encoded(document['start'])!=encoded(case_document(start)) or encoded(document['target'])!=encoded(case_document(target)):raise ValueError('target interpolation differs')
        verify_continuation(folder/'primary');native=read_json(folder/'primary/result.json');reference=read_json(folder/'reference.json')
        if native['options']!=p['options'] or reference['options']!=p['options'] or reference['producer_sha256']!=package_fingerprint() or not reference['callback_solver']:raise ValueError('reference controller contract differs')
        accepted=None;progress=0.
        for step in reference['attempts']:
            value=step['progress']
            if not 0<=value<=1 or (accepted is None and value!=0) or (accepted is not None and value<=progress):raise ValueError('invalid reference path')
            if step['previous_accepted_progress']!=(None if accepted is None else progress):raise ValueError('reference rollback differs')
            audit=physical_audit(point(start,target,value,accepted),step['result'],options)
            ok=bool(audit and audit['balance_acceptable'] and step.get('discard_reason') is None)
            if audit!=step['audit'] or ok!=step['accepted']:raise ValueError('reference step physics differs')
            if ok:accepted=step['result'];progress=value
        if reference['accepted_progress']!=progress or reference['completed']!=(progress==1):raise ValueError('reference target conclusion differs')
        if reference['completed'] and not physical_audit(point(start,target,1.),reference['final_result'],options)['balance_acceptable']:raise ValueError('reference final target invalid')
        row=case_row(start,target,factor,native,reference)
        if row!=read_json(folder/'comparison.json'):raise ValueError('comparison differs')
        rows.append(row)
    expected=dict(producer_sha256=package_fingerprint(),source_unchanged=True,cases=rows,all_targets_qualified=all(row['target_qualified'] for row in rows),
        original_90_110_points_closed=all(row['target_qualified'] for row in rows if row['load_factor'] in [.9,1.1]),
        independent_controller=False,independent_terminal_balance=True,stability_proven=False,voltage_collapse_proven=False,superiority_proven=False)
    if r!=expected:raise ValueError('continuation corpus conclusions differ')
    return dict(verified=True,manifest_sha256=identity,all_targets_qualified=r['all_targets_qualified'],qualified_load_factors=[row['load_factor'] for row in rows if row['target_qualified']],
        original_90_110_points_closed=r['original_90_110_points_closed'],scope='recomputed recorded paths/rollback balances and matched final states; no Newton/time replay or source authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out');p.add_argument('--verify');a=p.parse_args()
    if a.verify:print(json.dumps(verify_result(a.verify)));raise SystemExit(0)
    if not a.out:p.error('--out required')
    r=run(a.out);print(json.dumps(r));raise SystemExit(0 if r['all_targets_qualified'] else 2)

"""Analytic fold plus independent pandapower-equation RTE branch checks.

Reference uses its own makeYbus/makeSbus/dSbus_dV and base Newton solve.
The arclength controller, metric and rendering are shared and declared.
"""
import argparse,hashlib,json
from dataclasses import replace
from pathlib import Path
import numpy as np
from power_grid_env.ac_pseudo_arc import PseudoArcOptions,trace_power_flow,run_pseudo_arc,verify_pseudo_arc,audit_trace,PolarFamily
from power_grid_env.balanced_ac import case_from_document,case_document
from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
from power_grid_env.ac_continuation import point
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.pseudo_arc_reference import ReferenceBackend
from benchmarks.ac_continuation_acceptance import normalized_state
from benchmarks.ieee_ac_comparison import independent_balance,normalize_primary,compare_outputs

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'cases/ac-continuation/reference-inputs'


def analytic_cases():
    a=OpfCase('closed-form-two-bus',1.,[Bus(0,3,0.,0.,0.,0.,1.,0.,.02,2.),Bus(1,1,.1,0.,0.,0.,1.,0.,.02,2.)],
        [GeneratorSpec(0,-100,100,-100,100,(0.,),pg=0.,qg=0.)],[Branch(0,1,0.,1.,0.,100.,0.,0.,1,-180,180)])
    return a,replace(a,buses=[a.buses[0],replace(a.buses[1],pd=.7)])


def external_balance(start,target,r):
    family=PolarFamily(start,target);errors=[]
    for p in r['points']:
        c=family.load_case(p['lambda_value']);errors.append(independent_balance(case_document(c),normalized_state(c,p['result'])))
    for turn in r['turns']:
        p=turn['refinement']['estimate']
        if p is not None:
            c=family.load_case(p['lambda_value']);errors.append(independent_balance(case_document(c),normalized_state(c,p['result'])))
    return dict(passed=bool(errors) and max(errors)<=1e-6,maximum_terminal_balance_mva=max(errors,default=None),checked_states=len(errors))



def level_row(start,target,factor,level,native,reference):
    na=external_balance(start,target,native);ra=external_balance(start,target,reference)
    turns_n=[t['refinement']['estimate']['lambda_value'] for t in native['turns'] if t['refinement']['estimate']]
    turns_r=[t['refinement']['estimate']['lambda_value'] for t in reference['turns'] if t['refinement']['estimate']]
    comparison=None
    if native['completed'] and reference['completed']:
        comparison=compare_outputs(dict(case=case_document(target)),dict(result=normalize_primary(case_document(target),native['final_result'])),dict(result=reference['final_result']['normalized']))
    matched_turns=bool(turns_n) and len(turns_n)==len(turns_r) and max(abs(x-y) for x,y in zip(turns_n,turns_r))<=2e-6
    qualified=na['passed'] and ra['passed'] and (matched_turns if factor in [.9,1.1] else bool(comparison and comparison['passed']))
    row=dict(load_factor=factor,level=level,passed=bool(qualified),primary_completed=native['completed'],reference_completed=reference['completed'],
        primary_reason=native['reason'],reference_reason=reference['reason'],native_turn_lambda=turns_n,reference_turn_lambda=turns_r,
        primary_balance=na,reference_balance=ra,target_comparison=comparison,target_absence_proven=False)
    return row


def run(out):
    import pandapower as pp
    if pp.__version__!='3.5.6':raise ValueError('pinned reference required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint()
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['power_grid_env','benchmarks'] for p in (ROOT/folder).glob('*.py')}
    pp_root=Path(pp.__file__).parent;reference_sources={str(p.relative_to(pp_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in pp_root.rglob('*.py')}
    def write(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(value))
    origin=read_json(DATA/'origin.json')
    if any(hashlib.sha256((DATA/n).read_bytes()).hexdigest()!=h for n,h in origin['files'].items()):raise ValueError('frozen input mismatch')
    write('protocol.json',dict(producer_sha256=code,source_sha256=sources,reference_sources=reference_sources,input_origin=origin,
        analytic_reference='X=1,E=1,Q=0: Im(V)=-P, Re(V)^2-Re(V)+P^2=0; Pmax=.5, lambda=(.5-.1)/(.7-.1)=2/3',
        branch_goals=[.9,1.1],reachable_goals=[.95,1.05],step_pairs=[[.04,.06],[.02,.03]],turn_lambda_agreement_limit=2e-6,
        shared_controller=True,independent_reference_equations=True,matpower_runcpf_executed=False,independent_initializer=False,shared_frozen_dc_seed=True,
        scope='finite branch tracing and step refinement; no throughput ranking, global root exclusion, stability or voltage-collapse proof'))
    analytic=[];a,b=analytic_cases()
    for step in [.04,.02]:
        folder='analytic-'+str(step);d=dict(start=case_document(a),target=case_document(b),options=dict(step=step,maximum_step=step))
        r=run_pseudo_arc(d,out/folder);verify_pseudo_arc(out/folder)
        errors=[]
        for p in r['points']:
            voltage=p['result']['vm_pu'][1]*np.exp(1j*np.deg2rad(p['result']['va_deg'][1]));load=.1+.6*p['lambda_value']
            errors.extend([abs(voltage.imag+load),abs(voltage.real**2-voltage.real+load**2)])
        error=max(errors)
        estimate=r['turns'][0]['refinement']['estimate'];delta=abs(estimate['lambda_value']-2/3)
        row=dict(step=step,passed=error<=1e-8 and delta<=1e-7 and r['points'][-1]['result']['vm_pu'][1]<2**-.5,
            maximum_rectangular_equation_error=error,refined_lambda_error=delta);analytic.append(row)
    base=read_json(DATA/'rte-100.json');start=case_from_document(base['case']);write('base.json',base);rows=[]
    for factor in [.9,1.1,.95,1.05]:
        extreme=.9 if factor<1 else 1.1;endpoint=read_json(DATA/('rte-'+str(round(extreme*100))+'.json'))
        target=replace(point(start,case_from_document(endpoint['case']),(factor-1)/(extreme-1)),name='rte-arc-'+str(round(factor*100)))
        data=[]
        for level,(step,maximum) in enumerate([(.04,.06),(.02,.03)] if factor in [.9,1.1] else [(.04,.06)]):
            folder=str(round(factor*100))+'/'+str(level);document=dict(start=case_document(start),target=case_document(target),options=dict(step=step,maximum_step=maximum))
            native=run_pseudo_arc(document,out/folder/'primary');verify_pseudo_arc(out/folder/'primary')
            reference=trace_power_flow(start,target,document['options'],_backend=ReferenceBackend(base,factor))
            audit_trace(document,reference,allow_reference=True);write(folder+'/reference.json',reference)
            row=level_row(start,target,factor,level,native,reference)
            write(folder+'/comparison.json',row);data.append(row)
        refinement=bool(factor not in [.9,1.1] or (len(data)==2 and all(len(d['native_turn_lambda'])==1 and len(d['reference_turn_lambda'])==1 for d in data) and
            abs(data[0]['native_turn_lambda'][0]-data[1]['native_turn_lambda'][0])<=2e-6 and abs(data[0]['reference_turn_lambda'][0]-data[1]['reference_turn_lambda'][0])<=2e-6))
        rows.append(dict(load_factor=factor,passed=all(d['passed'] for d in data) and refinement,step_refinement_agrees=refinement,levels=data))
    unchanged=code==package_fingerprint() and all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in sources.items()) and all(hashlib.sha256((pp_root/n).read_bytes()).hexdigest()==h for n,h in reference_sources.items())
    result=dict(passed=unchanged and all(a['passed'] for a in analytic) and all(r['passed'] for r in rows),producer_sha256=code,source_unchanged=unchanged,
        analytic=analytic,cases=rows,original_90_110_points_closed=all(all(d['primary_completed'] and d['reference_completed'] and d['target_comparison'] and d['target_comparison']['passed'] for d in row['levels']) for row in rows if row['load_factor'] in [.9,1.1]),target_absence_proven=False,stability_proven=False,voltage_collapse_proven=False,independent_controller=False,matpower_runcpf_executed=False)
    write('summary.json',result);seal(out);return result


def verify_result(directory):
    directory=Path(directory);identity=verify(directory);p=read_json(directory/'protocol.json');summary=read_json(directory/'summary.json')
    if p['producer_sha256']!=package_fingerprint() or p['branch_goals']!=[.9,1.1] or p['reachable_goals']!=[.95,1.05] or p['step_pairs']!=[[.04,.06],[.02,.03]] or p['turn_lambda_agreement_limit']!=2e-6:raise ValueError('registered pseudo-arc corpus required')
    if p['source_sha256']['benchmarks/pseudo_arc_acceptance.py']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('branch benchmark source differs')
    analytic=[]
    for step in [.04,.02]:
        folder=directory/('analytic-'+str(step));verify_pseudo_arc(folder);r=read_json(folder/'result.json');errors=[]
        for point_ in r['points']:
            v=point_['result']['vm_pu'][1]*np.exp(1j*np.deg2rad(point_['result']['va_deg'][1]));load=.1+.6*point_['lambda_value']
            errors.extend([abs(v.imag+load),abs(v.real**2-v.real+load**2)])
        delta=abs(r['turns'][0]['refinement']['estimate']['lambda_value']-2/3);error=max(errors)
        analytic.append(dict(step=step,passed=error<=1e-8 and delta<=1e-7 and r['points'][-1]['result']['vm_pu'][1]<2**-.5,maximum_rectangular_equation_error=error,refined_lambda_error=delta))
    origin=p['input_origin']
    if any(hashlib.sha256((DATA/n).read_bytes()).hexdigest()!=h for n,h in origin['files'].items()):raise ValueError('frozen reference data differs')
    base=read_json(directory/'base.json')
    if encoded(base)!=encoded(read_json(DATA/'rte-100.json')):raise ValueError('frozen base differs')
    start=case_from_document(base['case']);rows=[]
    for factor in [.9,1.1,.95,1.05]:
        data=[]
        for level in range(2 if factor in [.9,1.1] else 1):
            folder=directory/str(round(factor*100))/str(level);document=read_json(folder/'primary/input.json')
            if encoded(document['start'])!=encoded(case_document(start)):raise ValueError('branch base differs')
            extreme=.9 if factor<1 else 1.1
            full=case_from_document(read_json(DATA/('rte-'+str(round(extreme*100))+'.json'))['case'])
            expected_target=replace(point(start,full,(factor-1)/(extreme-1)),name='rte-arc-'+str(round(factor*100)))
            options=dict(step=.04 if level==0 else .02,maximum_step=.06 if level==0 else .03)
            if encoded(document['target'])!=encoded(case_document(expected_target)) or document.get('options')!=options:raise ValueError('registered load path/arc steps differ')
            target=case_from_document(document['target']);verify_pseudo_arc(folder/'primary')
            native=read_json(folder/'primary/result.json');reference=read_json(folder/'reference.json')
            if reference['backend']!='pandapower-equations-3.5.6' or reference['options']!=native['options'] or native['options']!=PseudoArcOptions(**options).model_dump(mode='json'):raise ValueError('reference equations/options differ')
            audit_trace(document,reference,allow_reference=True)
            row=level_row(start,target,factor,level,native,reference)
            if row!=read_json(folder/'comparison.json'):raise ValueError('branch comparison differs')
            data.append(row)
        refinement=bool(factor not in [.9,1.1] or (len(data)==2 and all(len(d['native_turn_lambda'])==1 and len(d['reference_turn_lambda'])==1 for d in data) and abs(data[0]['native_turn_lambda'][0]-data[1]['native_turn_lambda'][0])<=2e-6 and abs(data[0]['reference_turn_lambda'][0]-data[1]['reference_turn_lambda'][0])<=2e-6))
        rows.append(dict(load_factor=factor,passed=all(d['passed'] for d in data) and refinement,step_refinement_agrees=refinement,levels=data))
    expected=dict(passed=all(a['passed'] for a in analytic) and all(r['passed'] for r in rows),producer_sha256=package_fingerprint(),source_unchanged=True,analytic=analytic,cases=rows,
        original_90_110_points_closed=all(all(d['primary_completed'] and d['reference_completed'] and d['target_comparison'] and d['target_comparison']['passed'] for d in row['levels']) for row in rows if row['load_factor'] in [.9,1.1]),
        target_absence_proven=False,stability_proven=False,voltage_collapse_proven=False,independent_controller=False,matpower_runcpf_executed=False)
    if expected!=summary:raise ValueError('branch acceptance conclusions differ')
    return dict(verified=True,manifest_sha256=identity,branch_validation_passed=summary['passed'],original_90_110_points_closed=summary['original_90_110_points_closed'],target_absence_proven=False,scope='recomputed stored curves/tangents/hyperplanes/independent balance and reference comparisons; not solver replay or authorship authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out');p.add_argument('--verify');a=p.parse_args()
    if a.verify:print(json.dumps(verify_result(a.verify)));raise SystemExit(0)
    if not a.out:p.error('--out required')
    r=run(a.out);print('branch validation passed:',r['passed']);raise SystemExit(0 if r['passed'] else 2)

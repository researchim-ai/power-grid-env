"""Finite independent nonlinear perturbation checks on IEEE300 upper branch."""
import argparse,hashlib,json
from dataclasses import replace
from pathlib import Path
import numpy as np
from scipy.sparse.linalg import splu
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.balanced_ac import case_from_document,case_document,branch_results
from power_grid_env.ac_dispatch import solve_dispatch
from power_grid_env.ac_loading_screen import run_loading_screen,verify_loading_screen,study_input
from power_grid_env.ac_sensitivity import run_sensitivities,verify_sensitivities
from power_grid_env.evidence import package_fingerprint
from benchmarks.ac_loading_acceptance import document as loading_document,check_reference_path
from benchmarks.ac_independent_arc import ReferenceArc
from benchmarks.ac_dispatch_sparse_reference import SparseReference
from benchmarks.ac_dispatch_acceptance import score
ROOT=Path(__file__).resolve().parents[1];FACTORS=(.95,.97,.972,.97207);ATOL=1e-6;RTOL=2e-3


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/ac_independent_arc.py',ROOT/'benchmarks/ac_dispatch_sparse_reference.py',ROOT/'benchmarks/ac_loading_acceptance.py',ROOT/'benchmarks/ac_dispatch_acceptance.py',ROOT/'cases/ac-scale/ieee300.json']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def vector(model,result):
    return np.r_[np.array(result['angle_rad'])[model.angles],result['voltage_pu'],result['delta_mw']/model.base]


def modes(model,x):
    vm,a,delta=model.unpack(x);v=vm*np.exp(1j*a);q=(v*np.conj(model.y@v)).imag+model.qd;z=q-(vm-model.vset);raw=model.pg0+model.alpha*delta
    return [np.where(model.phi==model.plo,2,np.where(raw<model.plo,-1,np.where(raw>model.phi,1,0))).tolist(),np.where(~model.control,3,np.where(model.qh==model.ql,2,np.where(z<model.ql,-1,np.where(z>model.qh,1,0)))).tolist()]


def independent_solve(case,options,seed):
    model=SparseReference(case,options);x=vector(model,seed['result'])
    for _ in range(80):
        f,j=model.evaluate(x,True);norm=float(max(abs(f)))
        if norm<=1e-12:return dict(result=model.render(x),maximum_residual_pu=norm,modes=modes(model,x))
        try:dx=splu(j).solve(-f)
        except RuntimeError:break
        for damping in [1.,.5,.25,.125,.0625,.03125,.015625,.0078125]:
            trial=x+damping*dx;vm,_,_=model.unpack(trial)
            if np.all((vm>.02)&(vm<3)) and max(abs(model.evaluate(trial)))<norm:x=trial;break
        else:break
    raise ValueError('independent perturbed solve failed')


def validate_reference(case,options,reference):
    model=SparseReference(case,options);x=vector(model,reference['result']);norm=float(max(abs(model.evaluate(x))))
    if norm>2e-10:raise ValueError('independent reference equations differ')
    rendered=model.render(x)
    for key in ['voltage_pu','angle_rad','pg_mw','generation_by_bus','branch_power_mva']:
        if not np.allclose(rendered[key],reference['result'][key],atol=1e-8,rtol=1e-10):raise ValueError('independent reference rendering differs')
    if modes(model,x)!=reference['modes']:raise ValueError('reference active set differs')


def output_values(case,reference,outputs):
    r=reference['result'];idx={b.id:i for i,b in enumerate(case.buses)};values={}
    for o in outputs:
        kind=o['kind']
        if kind=='voltage':value=r['voltage_pu'][idx[o['bus_id']]]
        elif kind=='angle':value=r['angle_rad'][idx[o['bus_id']]]
        elif kind=='pg':value=r['pg_mw'][o['generator_index']]
        elif kind=='delta':value=r['delta_mw']
        else:
            k=0 if o['terminal']=='from' else 2;p,q=r['branch_power_mva'][o['branch_index']][k:k+2];line=case.branches[o['branch_index']];bus=line.fbus if k==0 else line.tbus
            value={'branch_p':p,'branch_q':q,'branch_mva':np.hypot(p,q),'branch_current':np.hypot(p,q)/case.base_mva/r['voltage_pu'][idx[bus]]}[kind]
        values[o['name']]=float(value)
    return values


def changed(case,direction,amount):
    loads={p['bus_id']:p for p in direction['loads']}
    return replace(case,buses=[replace(b,pd=b.pd+amount*loads.get(b.id,{}).get('dp_mw',0.),qd=b.qd+amount*loads.get(b.id,{}).get('dq_mvar',0.)) for b in case.buses])


def predicted(result,direction):
    values={}
    for metric in result['adjoint']:
        g={r['bus_id']:r for r in metric['load_gradients']}
        values[metric['name']]=sum(g[p['bus_id']]['per_mw']*p.get('dp_mw',0.)+g[p['bus_id']]['per_mvar']*p.get('dq_mvar',0.) for p in direction['loads'])
    return values


def record_design(d,result):
    directions=list(d['directions'])
    for kind in ['p','q']:
        bus=result['adjoint'][0]['ranked_'+kind+'_buses'][0]
        directions.append(dict(name='ranked_'+kind,loads=[dict(bus_id=bus,**({'dp_mw':1.} if kind=='p' else {'dq_mvar':1.}))]))
    return [(direction,h) for direction in directions for h in ([1e-7,5e-8] if direction['name']=='uniform_growth' else [1e-4,5e-5])]


def validation(d,result,base,records):
    design=record_design(d,result)
    if len(records)!=len(design) or any(r['direction']!=direction or r['step']!=h for r,(direction,h) in zip(records,design,strict=True)):raise ValueError('registered perturbation inventory differs')
    case=case_from_document(d['case']);validate_reference(case,d['dispatch'],base)
    primary=dict(result['state']);primary.setdefault('branches',branch_results(case,np.array(primary['vm_pu']),np.deg2rad(primary['va_deg'])))
    base_comparison=score(d['case'],primary,base);checks=[]
    for record in records:
        direction=record['direction'];h=record['step'];values=[]
        for sign,ref in zip([-1,1],record['references'],strict=True):
            c=changed(case,direction,sign*h);validate_reference(c,d['dispatch'],ref)
            if ref['modes']!=base['modes']:raise ValueError('perturbation crossed a control boundary')
            values.append(output_values(c,ref,d['outputs']))
        expected=predicted(result,direction);fd={name:(values[1][name]-values[0][name])/(2*h) for name in expected}
        errors={name:abs(expected[name]-fd[name]) for name in expected};ratio=max(errors[k]/(ATOL+RTOL*abs(fd[k])) for k in errors)
        checks.append(dict(direction=direction['name'],step=h,maximum_scaled_error=float(ratio),passed=ratio<=1.,finite_difference=fd,absolute_errors=errors))
    consistency=max((abs(row['outputs'][name]-predicted(result,next(p for p in d['directions'] if p['name']==row['name']))[name])/(1+abs(row['outputs'][name])) for row in result['forward'] for name in row['outputs']),default=0.)
    return dict(base_comparison=base_comparison,checks=checks,forward_adjoint_relative_difference=consistency,passed=bool(base_comparison['passed'] and checks and all(c['passed'] for c in checks) and consistency<=1e-8))


def selected_outputs(start,initial):
    weak=start.buses[int(np.argmin(initial['result']['voltage_pu']))].id;line=int(np.argmax([np.hypot(*p[:2]) for p in initial['result']['branch_power_mva']]))
    outputs=[dict(name='weak_voltage',kind='voltage',bus_id=weak),dict(name='weak_angle',kind='angle',bus_id=weak),dict(name='delta',kind='delta'),dict(name='p0',kind='pg',generator_index=0)]
    outputs.extend(dict(name=kind+'_'+terminal,kind=kind,branch_index=line,terminal=terminal) for terminal in ['from','to'] for kind in ['branch_p','branch_q','branch_mva','branch_current'])
    return weak,line,outputs


def selected_directions(original,weak):
    return [dict(name='weak_p',loads=[dict(bus_id=weak,dp_mw=1.)]),dict(name='weak_q',loads=[dict(bus_id=weak,dq_mvar=1.)]),dict(name='uniform_growth',loads=[dict(bus_id=b.id,dp_mw=b.pd,dq_mvar=b.qd) for b in original.buses])]


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();raw=loading_document(.02);native=study_input(raw);start=case_from_document(native['start']);target=case_from_document(native['target']);options=native['options']['dispatch'];reference=ReferenceArc(start,target,options);path=reference.trace(options);(out/'reference-path.json').write_bytes(encoded(path));rows=[]
    original=case_from_document(raw['case']);initial=reference.at_lambda(path,0.,1);weak,line,outputs=selected_outputs(start,initial)
    for factor in FACTORS:
        folder=out/str(factor);folder.mkdir();case=replace(original,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in original.buses])
        if factor==.95:
            solved=solve_dispatch(case,options)
            if not solved['completed']:raise ValueError('base state failed')
            state=solved['result']
        else:
            loading=dict(raw,requested_load_factor=factor);run_loading_screen(loading,folder/'branch');verify_loading_screen(folder/'branch');state=read_json(folder/'branch/study/trace.json')['final_result']
        directions=selected_directions(original,weak)
        d=dict(case=case_document(case),dispatch=options,state=state,directions=directions,outputs=outputs);result=run_sensitivities(d,folder/'sensitivity');verify_sensitivities(folder/'sensitivity')
        if not result['completed']:raise ValueError('local sensitivity refused: '+result['reason'])
        base=reference.at_lambda(path,(factor-.95)/.05,1);base=independent_solve(case,options,base);extra=[]
        for kind in ['p','q']:
            bus=result['adjoint'][0]['ranked_'+kind+'_buses'][0];extra.append(dict(name='ranked_'+kind,loads=[dict(bus_id=bus,**({'dp_mw':1.} if kind=='p' else {'dq_mvar':1.}))]))
        records=[]
        for direction in [*directions,*extra]:
            for h in ([1e-7,5e-8] if direction['name']=='uniform_growth' else [1e-4,5e-5]):
                refs=[independent_solve(changed(case,direction,sign*h),options,base) for sign in [-1,1]];records.append(dict(direction=direction,step=h,references=refs))
        evidence=dict(base=base,records=records);(folder/'validation.json').write_bytes(encoded(evidence));check=validation(d,result,base,records)
        row=dict(load_factor=factor,voltage_bus_id=weak,branch_index=line,voltage_pu=result['adjoint'][0]['value'],maximum_abs_voltage_load_gradient=max(max(abs(g['per_mw']),abs(g['per_mvar'])) for g in result['adjoint'][0]['load_gradients']),**check);rows.append(row);print(json.dumps({k:row[k] for k in ['load_factor','voltage_pu','maximum_abs_voltage_load_gradient','passed']}),flush=True)
    summary=dict(schema='pge-ac-sensitivity-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,derivative_absolute_tolerance=ATOL,derivative_relative_tolerance=RTOL,passed=bool(all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()),reference='own independent CPF seed and PP-stamp nonlinear perturbations, two central-difference steps',condition_number_claimed=False,derivatives_through_switching=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-ac-sensitivity-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current sensitivity producers required')
    raw=loading_document(.02);native=study_input(raw);start=case_from_document(native['start']);target=case_from_document(native['target']);original=case_from_document(raw['case']);ref=ReferenceArc(start,target,native['options']['dispatch']);path=read_json(out/'reference-path.json');check_reference_path(ref,path)
    initial_u=np.array(path['points'][0]['u']);ref.evaluate(initial_u);initial=dict(result=ref.model.render(initial_u[:-1]*ref.scale));weak,line,outputs=selected_outputs(start,initial)
    for factor in FACTORS:
        folder=out/str(factor);verify_sensitivities(folder/'sensitivity');d=read_json(folder/'sensitivity/input.json');result=read_json(folder/'sensitivity/result.json');e=read_json(folder/'validation.json');check=validation(d,result,e['base'],e['records']);row=next(r for r in s['rows'] if r['load_factor']==factor)
        expected_case=case_document(replace(original,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in original.buses]))
        if encoded(d['case'])!=encoded(expected_case) or d['dispatch']!=native['options']['dispatch']:raise ValueError('registered sensitivity operating point differs')
        expected_doc=dict(case=expected_case,dispatch=native['options']['dispatch'],state=d['state'],directions=selected_directions(original,weak),outputs=outputs)
        if encoded(d)!=encoded(expected_doc):raise ValueError('registered sensitivity request differs')
        if factor!=.95:
            verify_loading_screen(folder/'branch')
            if encoded(read_json(folder/'branch/input.json'))!=encoded(dict(raw,requested_load_factor=factor)) or encoded(d['state'])!=encoded(read_json(folder/'branch/study/trace.json')['final_result']):raise ValueError('native branch provenance differs')
        recomputed=dict(load_factor=factor,voltage_bus_id=d['outputs'][0]['bus_id'],branch_index=d['outputs'][4]['branch_index'],voltage_pu=result['adjoint'][0]['value'],maximum_abs_voltage_load_gradient=max(max(abs(g['per_mw']),abs(g['per_mvar'])) for g in result['adjoint'][0]['load_gradients']),**check)
        if row!=recomputed:raise ValueError('sensitivity comparison differs')
        rows.append(recomputed)
    if s['rows']!=rows or s['passed']!=all(r['passed'] for r in rows) or s['derivative_absolute_tolerance']!=ATOL or s['derivative_relative_tolerance']!=RTOL or any(s[k] for k in ['condition_number_claimed','derivatives_through_switching','superiority_proven']):raise ValueError('sensitivity conclusion differs')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,scope='native derivatives and stored independent nonlinear perturbations; no conditioning certificate or solver replay')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

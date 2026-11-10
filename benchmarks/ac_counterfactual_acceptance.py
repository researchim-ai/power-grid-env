"""Finite counterfactual validation against a separately initialized PP reference."""
import argparse,hashlib,json
from pathlib import Path
from dataclasses import replace
import numpy as np
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.balanced_ac import case_from_document,case_document
from power_grid_env.ac_counterfactual import run_counterfactual,verify_counterfactual,CounterfactualInput,cases,native_input
from power_grid_env.ac_dispatch import solve_dispatch
from benchmarks.ac_loading_acceptance import document as loading_document,check_reference_path,branch_side
from power_grid_env.ac_loading_screen import study_input
from benchmarks.ac_independent_arc import ReferenceArc
from benchmarks.ac_dispatch_sparse_reference import solve_reference
from benchmarks.ac_sensitivity_acceptance import independent_solve,validate_reference
from benchmarks.ac_dispatch_arc_acceptance import states,compare
from power_grid_env.ac_pseudo_arc import PseudoArcOptions,make_family
ROOT=Path(__file__).resolve().parents[1]
SPECS=('ieee-smooth','ieee-q-switch','ieee-relief','ieee-fold','p-entry','p-release')


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/ac_independent_arc.py',ROOT/'benchmarks/ac_dispatch_sparse_reference.py',ROOT/'benchmarks/ac_loading_acceptance.py',ROOT/'benchmarks/ac_sensitivity_acceptance.py',ROOT/'benchmarks/ac_dispatch_arc_acceptance.py',ROOT/'benchmarks/ac_dispatch_acceptance.py',ROOT/'benchmarks/ieee_ac_comparison.py',ROOT/'cases/ac-scale/ieee300.json',ROOT/'cases/ac-dispatch-arc/analytic-entry.json',ROOT/'cases/ac-dispatch-arc/analytic-release.json']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def registered(name,seed_case=None,seed_state=None):
    if name.startswith('ieee'):
        factor,target={'ieee-smooth':(.97,.9701),'ieee-q-switch':(.97,.971),'ieee-relief':(.97,.969),'ieee-fold':(.97,1.)}[name]
        original=case_from_document(loading_document(.02)['case']);base=replace(original,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in original.buses]);change=target-factor
        config=loading_document(.02)['options'];options=PseudoArcOptions.model_validate(config).model_dump(mode='json')
        if seed_case is not None and encoded(case_document(base))!=encoded(case_document(seed_case)):raise ValueError('seed case differs')
        return dict(case=case_document(base),state=seed_state,changes=[dict(bus_id=b.id,dp_mw=b.pd,dq_mvar=b.qd) for b in original.buses],amount=change,options=options,outputs=[dict(name='voltage235',kind='voltage',bus_id=235),dict(name='pg68',kind='pg',generator_index=68),dict(name='delta',kind='delta'),dict(name='branch_p',kind='branch_p',branch_index=399,terminal='from'),dict(name='current',kind='branch_current',branch_index=399,terminal='from')])
    source=read_json(ROOT/'cases/ac-dispatch-arc'/('analytic-entry.json' if name=='p-entry' else 'analytic-release.json'));a=case_from_document(source['start']);b=case_from_document(source['target'])
    return dict(case=source['start'],changes=[dict(bus_id=x.id,dp_mw=y.pd-x.pd,dq_mvar=y.qd-x.qd) for x,y in zip(a.buses,b.buses)],amount=1.,options=source['options'],monitor=source['monitor'],outputs=[dict(name='p0',kind='pg',generator_index=0),dict(name='v0',kind='voltage',bus_id=0)])


def compare_study(d,folder,reference,path,stored=None):
    native=native_input(CounterfactualInput.model_validate(d),read_json(folder/'base.json')['state']);o=PseudoArcOptions.model_validate(native['options']);a=case_from_document(native['start']);b=case_from_document(native['target']);family=make_family(a,b,o);study=dict(trace=read_json(folder/'study/trace.json'),envelope=read_json(folder/'study/envelope.json'));sampled=states(study);checks=[];refs=[]
    if stored is not None and len(stored)!=len(sampled):raise ValueError('reference state inventory differs')
    baseline=None
    if reference is None:baseline=solve_reference(a,native['options']['dispatch']) if stored is None else None
    for i,(label,lam,state) in enumerate(sampled):
        if stored is not None:
            item=stored[i]
            if item['label']!=label:raise ValueError('reference label differs')
            ref=item['reference']
        elif reference is not None:
            base_factor=.97;target_factor=base_factor+d['amount'];global_lam=(base_factor+lam*(target_factor-base_factor)-.95)/.05
            side=1 if label=='target' or d['amount']<0 else branch_side(study,label)
            ref=reference.at_lambda(path,global_lam,side)
        else:ref=independent_solve(family.load_case(lam),native['options']['dispatch'],baseline)
        # Render and audit reference using its own electrical implementation.
        if 'modes' not in ref:ref=independent_solve(family.load_case(lam),native['options']['dispatch'],ref) if stored is None else ref
        if 'modes' not in ref:raise ValueError('reference active-set evidence required')
        validate_reference(family.load_case(lam),native['options']['dispatch'],ref)
        c=compare(family,lam,state,ref)
        if stored is not None and item['comparison']!=c:raise ValueError('counterfactual comparison differs')
        refs.append(dict(label=label,reference=ref,comparison=c));checks.append(c)
    return refs,dict(states=len(checks),maximum_voltage_error_pu=max((c['errors']['voltage_pu'] for c in checks),default=0.),passed=bool(checks and all(c['passed'] for c in checks)))


def conclusion(name,folder,comparison):
    r=read_json(folder/'result.json');reached=name!='ieee-fold';switch=name in {'ieee-q-switch','p-entry','p-release'}
    return dict(case=name,requested_change_reached=r['requested_change_reached'],reason=r['reason'],linear_applicability=r['linear_applicability'],active_set_changed=r['active_set_changed'],prediction_errors={o['name']:dict(units=o['units'],absolute_error=None if o['linear_error'] is None else abs(o['linear_error'])) for o in r['outputs']},comparison=comparison,passed=bool(comparison['passed'] and r['requested_change_reached']==reached and (r['completed'] if reached else r['reason']=='sampled_turn_before_target') and (not switch or r['linear_applicability']=='observed_control_switch')))


def qualify(out):
    from power_grid_env.ac_loading_screen import run_loading_screen
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();loading=loading_document(.02);native=study_input(loading);a=case_from_document(native['start']);b=case_from_document(native['target']);ref=ReferenceArc(a,b,native['options']['dispatch']);path=ref.trace(native['options']['dispatch']);(out/'reference-path.json').write_bytes(encoded(path))
    seed_doc=dict(loading,requested_load_factor=.97);run_loading_screen(seed_doc,out/'seed');seed=read_json(out/'seed/study/trace.json')['final_result'];original=case_from_document(loading['case']);seed_case=replace(original,buses=[replace(b,pd=b.pd*.97,qd=b.qd*.97) for b in original.buses]);rows=[]
    for name in SPECS:
        d=registered(name,seed_case,seed);folder=out/name;run_counterfactual(d,folder);verify_counterfactual(folder);evidence=out/(name+'-references.json');refs,c=compare_study(d,folder,ref if name.startswith('ieee') else None,path);evidence.write_bytes(encoded(refs));row=conclusion(name,folder,c);rows.append(row);print(json.dumps(row),flush=True)
    s=dict(schema='pge-counterfactual-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,passed=bool(all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()),linear_prediction_used_as_endpoint=False,global_absence_proven=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    from power_grid_env.ac_loading_screen import verify_loading_screen
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-counterfactual-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current counterfactual producers required')
    loading=loading_document(.02);native=study_input(loading);ref=ReferenceArc(case_from_document(native['start']),case_from_document(native['target']),native['options']['dispatch']);path=read_json(out/'reference-path.json');check_reference_path(ref,path);verify_loading_screen(out/'seed')
    if encoded(read_json(out/'seed/input.json'))!=encoded(dict(loading,requested_load_factor=.97)):raise ValueError('registered seed differs')
    seed=read_json(out/'seed/study/trace.json')['final_result'];seed_case=case_from_document(read_json(out/'seed/study/input.json')['target'])
    for name in SPECS:
        folder=out/name;verify_counterfactual(folder);d=read_json(folder/'input.json');expected=registered(name,seed_case,seed)
        if encoded(d)!=encoded(expected):raise ValueError('registered finite scenario differs')
        _,c=compare_study(d,folder,ref if name.startswith('ieee') else None,path,read_json(out/(name+'-references.json')));rows.append(conclusion(name,folder,c))
    if s['rows']!=rows or s['passed']!=all(r['passed'] for r in rows) or any(s[k] for k in ['linear_prediction_used_as_endpoint','global_absence_proven','superiority_proven']):raise ValueError('counterfactual qualification conclusion differs')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,scope='audited finite branches, local predictions and stored separately initialized electrical reference comparisons; no global absence or source authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

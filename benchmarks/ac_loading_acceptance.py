"""IEEE300 constrained-turn investigation with a separately executed CPF."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.ac_loading_screen import run_loading_screen,verify_loading_screen,study_input
from power_grid_env.balanced_ac import case_from_document
from power_grid_env.ac_pseudo_arc import make_family,PseudoArcOptions
from benchmarks.ac_independent_arc import ReferenceArc
from benchmarks.ac_dispatch_arc_acceptance import states,compare,event_estimates,step_comparison
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'cases/ac-scale/ieee300.json';STEPS=(.03,.015)


def document(step):
    d=read_json(SOURCE);o=d['options'];o.update(step=step,maximum_step=step,tolerance_pu=1e-12,maximum_wall_s=180);o['dispatch']['tolerance_pu']=1e-12
    return dict(case=d['start'],starting_load_factor=.95,requested_load_factor=1.,options=o,monitor={},provenance=dict(origin='PP3.5.6 packaged IEEE300 pi-equivalent; unchanged P/Q bounds and Pg; all signed Pd/Qd scaled',source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest()))


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/ac_independent_arc.py',ROOT/'benchmarks/ac_dispatch_sparse_reference.py',ROOT/'benchmarks/ac_dispatch_arc_acceptance.py',ROOT/'benchmarks/ac_dispatch_acceptance.py',SOURCE]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def branch_side(study,label):
    parts=label.split(':');kind=parts[0];i=int(parts[1])
    if kind=='point':point=i
    elif kind in ('q','p'):
        event=study['trace']['control_changes' if kind=='q' else 'p_control_changes'][i];point=event['before_point' if parts[2]=='left' else 'after_point']
    elif kind=='limit':
        event=study['envelope']['events'][i];point=event['before_point' if parts[2]=='left' else 'after_point']
    else:raise ValueError('unsupported diagnostic state label')
    return 1 if study['trace']['points'][point]['tangent'][-1]>0 else -1


def check_reference_path(f,path):
    if path['primary_state_used'] or not path['points']:raise ValueError('invalid independent path claim')
    previous=None;maximum=0.
    for p in [*path['points'],path['turn']]:
        u=np.array(p['u']);t=np.array(p['tangent']);r,a=f.evaluate(u)
        error=max(float(max(abs(r))),float(max(abs(a@t))),abs(np.linalg.norm(t)-1.));maximum=max(maximum,error)
        if not f.valid(u) or error>1e-8:raise ValueError('independent path equations/tangent differ')
        if previous is not None and p is not path['turn']:
            before=np.array(previous['u']);bt=np.array(previous['tangent']);h=(u-before)@bt
            if t@bt<0 or not 0<h<=path['step']+1e-8:raise ValueError('independent path orientation/step differs')
        previous=p
    if abs(path['points'][0]['u'][-1])>1e-12 or abs(path['turn']['tangent'][-1])>1e-6:raise ValueError('independent start/turn differs')
    return maximum


def evaluate(folder,stored=False):
    d=read_json(folder/'input.json');native=study_input(d);o=PseudoArcOptions.model_validate(d['options']);start=case_from_document(native['start']);target=case_from_document(native['target']);f=make_family(start,target,o);ref=ReferenceArc(start,target,native['options']['dispatch'])
    if stored:path=read_json(folder/'reference-path.json')
    else:
        path=ref.trace(native['options']['dispatch']);(folder/'reference-path.json').write_bytes(encoded(path))
    check_reference_path(ref,path);study=dict(trace=read_json(folder/'study/trace.json'),envelope=read_json(folder/'study/envelope.json'));checks=[];refs=[];saved=read_json(folder/'references.json') if stored else None;sampled=states(study)
    if stored and len(saved)!=len(sampled):raise ValueError('reference inventory differs')
    for i,(label,lam,state) in enumerate(sampled):
        side=branch_side(study,label)
        if stored:
            entry=saved[i]
            if entry['label']!=label or entry['branch_side']!=side:raise ValueError('reference branch identity differs')
            r=entry['reference']
        else:r=ref.at_lambda(path,lam,side)
        comparison=compare(f,lam,state,r)
        if stored and comparison!=saved[i]['comparison']:raise ValueError('stored branch comparison differs')
        refs.append(dict(label=label,branch_side=side,reference=r,comparison=comparison));checks.append(comparison)
    if not stored:(folder/'references.json').write_bytes(encoded(refs))
    result=read_json(folder/'result.json');turn=result['sampled_turns'][0]['lambda_value'] if result['sampled_turns'] else None
    difference=None if turn is None else abs(turn-path['turn']['u'][-1]);events=event_estimates(study)
    return dict(step=d['options']['step'],states=len(checks),native_turn_lambda=turn,independent_turn_lambda=path['turn']['u'][-1],turn_difference=difference,estimated_turn_load_factor=result['sampled_turns'][0]['load_factor'] if turn is not None else None,maximum_voltage_error_pu=max((c['errors']['voltage_pu'] for c in checks),default=0.),events=events,passed=bool(result['completed'] and result['reason']=='sampled_turn_before_target' and difference is not None and difference<=2e-6 and checks and all(c['passed'] for c in checks) and all(e['localized'] for e in events)))


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();rows=[]
    for step in STEPS:
        folder=out/str(step);run_loading_screen(document(step),folder);verify_loading_screen(folder)
        # Native packet is already sealed; reference evidence is a separate sibling.
        evidence=out/(str(step)+'-evidence');evidence.mkdir()
        for name in ['input.json','result.json']:(evidence/name).write_bytes((folder/name).read_bytes())
        import shutil
        shutil.copytree(folder/'study',evidence/'study');row=evaluate(evidence);rows.append(row);seal(evidence);print(json.dumps(row),flush=True)
    error=abs(rows[0]['native_turn_lambda']-rows[1]['native_turn_lambda']);event_check=step_comparison('ieee300',rows);summary=dict(event_step_comparison=event_check,schema='pge-loading-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,step_turn_difference=error,passed=bool(all(r['passed'] for r in rows) and error<=2e-6 and event_check['passed'] and producer==package_fingerprint() and hashes==sources()),independent_continuation_executed=True,global_target_absence_proven=False,stability_proven=False,original_nominal_case_solved=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-loading-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current loading producers required')
    for step in STEPS:
        folder=out/str(step);verify_loading_screen(folder)
        if encoded(read_json(folder/'input.json'))!=encoded(document(step)):raise ValueError('registered loading input differs')
        evidence=out/(str(step)+'-evidence');verify(evidence)
        for name in ['input.json','result.json']:
            if (evidence/name).read_bytes()!=(folder/name).read_bytes():raise ValueError('evidence native identity differs')
        from power_grid_env.ac_envelope import verify_envelope
        verify_envelope(evidence/'study')
        if verify(evidence/'study')!=verify(folder/'study'):raise ValueError('evidence study differs')
        rows.append(evaluate(evidence,True))
    error=abs(rows[0]['native_turn_lambda']-rows[1]['native_turn_lambda']);event_check=step_comparison('ieee300',rows);passed=all(r['passed'] for r in rows) and error<=2e-6 and event_check['passed']
    if s['event_step_comparison']!=event_check or s['rows']!=rows or s['passed']!=passed or s['step_turn_difference']!=error or not s['independent_continuation_executed'] or any(s[k] for k in ['global_target_absence_proven','stability_proven','original_nominal_case_solved','superiority_proven']):raise ValueError('loading conclusion differs')
    return dict(verified=True,passed=passed,manifest_sha256=identity,scope='stored native physical audits, separate reference CPF equations/tangents and matched branch comparisons; no global absence or authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

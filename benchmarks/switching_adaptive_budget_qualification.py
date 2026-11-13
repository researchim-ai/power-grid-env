"""Sealed adaptive-budget self-consistency assessment, without new simulations.

Same physical caps/reduction factor as fixed-mesh qualification. A positive
result covers the registered inputs only, not absolute continuous-time error.
"""
import argparse
import hashlib
import math
from benchmarks.extended_closed_loop_acceptance import NAMES
from pathlib import Path
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal
from power_grid_env.switching_drive import SwitchingDriveStudy
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from power_grid_env.switching_adaptive import _source_tolerance,_motor_tolerance


def mesh_audit(study,result):
    cfg=study.adaptive
    if cfg is None:raise ValueError('adaptive event audit requires adaptive controls')
    info=result['integration'];events=result['integration_event_log']
    if info.get('absolute_error_certified') is not False:raise ValueError('invalid absolute error claim')
    def integer(key):
        value=info[key]
        if type(value) is not int or value<0:raise ValueError('invalid integration counter: '+key)
        return value
    trials=integer('trials');rejected=integer('rejections');pairs=integer('accepted_pairs');probes=integer('probe_evaluations')
    if trials>probes or rejected>trials or pairs>trials-rejected or type(result['metrics']['steps']) is not int or result['metrics']['steps']!=trials-rejected+pairs or not 0<probes<=cfg.trial_budget or integer('root_probes')>probes:raise ValueError('inconsistent adaptive work counters')
    count={'source_current_zero':0,'motor_current_zero':0};last=-1.;energy=0.
    for e in events:
        kind=e['kind'];time=e['time_s'];phases=e['phases']
        if kind not in count or type(time) not in (int,float) or not math.isfinite(time) or not last<=time<=study.duration_s or time<0:raise ValueError('invalid numerical event time/kind')
        last=time;count[kind]+=1
        if not isinstance(phases,list) or not phases or any(type(k) is not int or not 0<=k<3 for k in phases) or phases!=sorted(set(phases)):raise ValueError('invalid numerical event phases')
        motor=kind=='motor_current_zero';values=e['current_residual_a' if motor else 'current_correction_a'];tol=_motor_tolerance(cfg) if motor else _source_tolerance(cfg)
        if len(values)!=len(phases) or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>tol for v in values):raise ValueError('unresolved numerical current event')
        if motor:
            if e.get('flux_projection') is not False:raise ValueError('motor flux projection is outside this profile')
        else:
            correction=e['energy_correction_j']
            if type(correction) not in (int,float) or not math.isfinite(correction) or correction<0:raise ValueError('invalid source event energy')
            energy+=correction
    total=info['source_event_energy_correction_j']
    if integer('motor_boundaries')!=count['motor_current_zero'] or integer('source_boundaries')!=count['source_current_zero'] or type(total) not in (int,float) or not math.isfinite(total) or not math.isclose(energy,total,rel_tol=1e-12,abs_tol=1e-30):raise ValueError('numerical event accounting differs')
    return True


def assess(runs,reference_root,out):
    runs=Path(runs);identity=verify(runs);protocol=read_json(runs/'protocol.json');raw=read_json(runs/'summary.json');producer=package_fingerprint()
    if protocol['code_sha256']!=producer:raise ValueError('adaptive qualification producer differs')
    fractions=protocol['fractions']
    if len(fractions)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or not 1e-6<=v<=1 for v in fractions) or any(a/2!=b for a,b in zip(fractions,fractions[1:])):raise ValueError('three consecutive half budgets required')
    limits=SwitchingTemporalLimits.model_validate(protocol['limits'])
    if limits.model_dump(mode='json')!=SwitchingTemporalLimits().model_dump(mode='json'):raise ValueError('registered adaptive profile limits differ')
    rows=[];reference_hashes={};seen=set()
    for entry in raw['rows']:
        name=entry['case']
        if name not in NAMES or name not in protocol['references'] or name in seen or Path(name).name!=name:raise ValueError('invalid or duplicate case')
        seen.add(name);path=Path(reference_root)/(name+'.json');digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=protocol['references'][name]:raise ValueError('adaptive reference hash differs')
        reference_hashes[name]=digest;ref=read_json(path);levels=[];results=[];common=None;study=None
        for i,fraction in enumerate(fractions):
            folder=runs/name/f'level-{i}';study=SwitchingDriveStudy.model_validate(read_json(folder/'study.json'));doc=study.model_dump(mode='json')
            if study.adaptive is None or study.adaptive.error_budget_fraction!=fraction:raise ValueError('adaptive budget differs from protocol')
            doc['adaptive'].pop('error_budget_fraction')
            if common is not None and doc!=common:raise ValueError('study changed beyond the adaptive error budget')
            common=doc;r=read_json(folder/'result.json');mesh_audit(study,r);a=audited(study,r,limits);b=audited(study,ref,limits);c=differences(study,r,ref,limits)
            levels.append(dict(physics_audits=a,reference_audits=b,reference_difference=c));results.append(r)
        pair=[differences(study,results[i-1],results[i],limits) for i in [1,2]];reduction={}
        for k,v in pair[1]['groups'].items():
            old=pair[0]['groups'][k]['maximum_normalized'];new=v['maximum_normalized'];reduction[k]=dict(previous_normalized=old,current_normalized=new,passed=new<=max(limits.reduction_factor*old,1e-8))
        qualified=all(all(r['physics_audits'].values()) and all(r['reference_audits'].values()) and r['reference_difference']['passed'] for r in levels) and all(c['passed'] for c in pair) and all(v['passed'] for v in reduction.values())
        rows.append(dict(case=name,qualified=qualified,selected_error_budget_fraction=fractions[-1] if qualified else None,levels=levels,adjacent_differences=pair,reduction=reduction,mesh_events_audited=True))
    if not rows or package_fingerprint()!=producer:raise ValueError('empty or changed qualification producer')
    result=dict(schema='power-grid-env.switching-adaptive-budget-qualification/v1',qualified=all(r['qualified'] for r in rows),rows=rows,fractions=fractions,limits=limits.model_dump(mode='json'),code_sha256=producer,scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runs_manifest=identity,reference_sha256=reference_hashes,absolute_error_certified=False,independent_reference=False,scope='registered authored studies; three audited decreasing local budgets, fixed physical caps, decreasing adjacent differences, validated event residuals and work counters; archived production references; no absolute continuous-time/OEM or external ranking claim')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',required=True);p.add_argument('--reference-root',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=assess(a.runs,a.reference_root,a.out);print(r['qualified']);raise SystemExit(0 if r['qualified'] else 2)

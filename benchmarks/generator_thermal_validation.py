"""Preregistered thermal-trip physics: full independent SI, pre-trip ANDES.

Stock ANDES trips freeze different states. Its unprotected trajectory is used
only strictly before the first electrical opening, never as post-trip evidence.
"""
from benchmarks.event_sampling import native_sample_sides
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.generator_thermal_cases import NAMES,NEGATIVE
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.native_online_validation import STEPS,channel_limits,source_identity,qualify
from benchmarks.multibus_loaded_audit import current_closure
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]
LIMITS=dict(event_s=1e-6,analytic_s=1e-8,SI_state=1e-5,SI_power=100.,thermal=1e-6,KCL_pu=1e-6,state_jump=1e-10,off_current=1e-10,cooling=1e-8)


def load_case(name):return StudySpec.model_validate_json((ROOT/'cases/studies'/('generator_thermal_'+name+'.json')).read_text())


def baseline(spec):
    data=spec.model_dump(mode='json');data['coupled']['generator_thermal']=[]
    data['coupled']['events']=[e for e in data['coupled']['events'] if e['kind']!='generator_open']
    return StudySpec.model_validate(data)


def trips(report):return [e for e in report['event_log'] if e['kind']=='generator_thermal_trip']


def full_compare(spec,primary,si):
    errors={};limits={}
    for key,values in si['channels'].items():
        target=np.asarray(primary['channels'][key]);v=np.asarray(values)
        if target.shape!=v.shape or not np.isfinite(target).all() or not np.isfinite(v).all():raise ValueError('nonfinite/incomplete protected SI trace')
        errors[key]=float(np.max(abs(target-v)))
        discrete=key.endswith(('.connected','.stator_thermal_latched','.contactor_voltage_available','.contactor_pending_restart','.contactor_restart_count','.contactor_timer_active'))
        limits[key]=0. if discrete else LIMITS['SI_power'] if key.endswith(('.p_w','.q_var')) else LIMITS['thermal'] if key.endswith('stator_thermal_level') else LIMITS['SI_state']
    left,right=trips(primary),trips(si)
    if [e['equipment_id'] for e in left]!=[e['equipment_id'] for e in right]:raise ValueError('independent generator trip sequence differs')
    time_error=max((abs(a['time_s']-b['time_s']) for a,b in zip(left,right)),default=0.)
    return dict(passed=time_error<=LIMITS['event_s'] and all(e<=limits[k] for k,e in errors.items()),errors=errors,limits=limits,maximum_trip_time_error_s=time_error)


def physical_contract(spec,r):
    ch=r['channels'];times=np.array(r['time_s']);sides=r['sample_side'];jumps=[];cooling=[];analytic=[];off=[];checks=[]
    state_keys=[g.id+'.'+k for g in spec.network.generators for k in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_pu','SEXS.field_pu')]
    state_keys += [p.generator_id+'.stator_thermal_level' for p in spec.coupled.generator_thermal]
    for i,side in enumerate(sides):
        if side=='pre_event':
            if i+1>=len(times) or sides[i+1]!='post_event' or times[i]!=times[i+1]:raise ValueError('missing protected event pair')
            jumps.extend(abs(ch[k][i+1]-ch[k][i]) for k in state_keys)
    for p in spec.coupled.generator_thermal:
        gid=p.generator_id;opened=np.flatnonzero(np.asarray(ch[gid+'.connected'])==0)
        for e in trips(r):
            if e['equipment_id']!=gid:continue
            checks += [abs(e['level']-p.trip_level)<=1e-8,ch[gid+'.stator_thermal_latched'][-1]==1.]
        if len(opened):
            start=int(opened[0]);off.extend(abs(np.array(ch[gid+'.current_pu'])[opened]));theta=ch[gid+'.stator_thermal_level'];expected=theta[start]*np.exp(-(times[opened]-times[start])/p.time_constant_s)
            cooling.append(float(np.max(abs(np.array(theta)[opened]-expected))))
            # Rotor must continue; no whole-machine state freeze is accepted.
            checks.append(max(abs(np.array(ch[gid+'.delta'])[opened]-ch[gid+'.delta'][start]))>1e-5)
        if spec.id.endswith(('constant_cold','constant_warm','three_machine')):
            heat=ch[gid+'.current_pu'][0]**2;initial=ch[gid+'.stator_thermal_level'][0]
            expected=p.time_constant_s*math.log((heat-initial)/(heat-p.trip_level))
            e=next(e for e in trips(r) if e['equipment_id']==gid);analytic.append(abs(e['time_s']-expected))
    closure=current_closure(spec,r) if r.get('independent_SI_network') else None
    values=dict(maximum_state_jump=max(jumps,default=0.),maximum_off_current=max(off,default=0.),maximum_cooling_error=max(cooling,default=0.),maximum_analytic_trip_error_s=max(analytic,default=0.),maximum_KCL_pu=closure)
    passed=all(checks) and values['maximum_state_jump']<=LIMITS['state_jump'] and values['maximum_off_current']<=LIMITS['off_current'] and values['maximum_cooling_error']<=LIMITS['cooling'] and values['maximum_analytic_trip_error_s']<=LIMITS['analytic_s'] and (closure is None or closure<=LIMITS['KCL_pu'])
    return dict(passed=bool(passed),**values)


def pretrip_compare(spec,primary,si,native):
    if not native.get('completed'):return dict(completed=False,failure=native)
    openings=[e['time_s'] for e in primary['event_log'] if e['kind'] in ('generator_thermal_trip','generator_open')]
    stop=min(openings) if openings else None
    selected=[i for i,t in enumerate(primary['time_s']) if stop is None or t<stop-1e-9]
    if len(selected)<2:raise ValueError('insufficient native pre-trip samples')
    sample_sides=native_sample_sides(primary)
    times=[primary['time_s'][i] for i in selected];sides=[sample_sides[i] for i in selected];bounds=channel_limits(baseline(spec));errors={};limits={}
    for k,bound in bounds.items():
        v=sample_native(native,times,sides,k)
        for label,reference in [('primary',primary),('SI',si)]:
            errors[label+':'+k]=float(np.max(abs(v-np.array(reference['channels'][k])[selected])));limits[label+':'+k]=bound
    return dict(completed=True,step_s=native['step_s'],errors=errors,limits=limits,maximum_event_time_error_s=0.,scope='strictly_before_first_opening_only',samples=len(selected),last_compared_time_s=times[-1],first_opening_s=stop)


def run(out,batch_size=2):
    out=Path(out);sources=source_identity();code=package_fingerprint()
    protocol=dict(schema='power-grid-env.generator-stator-thermal/v1',cases=list(NAMES),negative_cases=list(NEGATIVE),case_sha256={n:canonical_hash(load_case(n)) for n in (*NAMES,*NEGATIVE)},baseline_sha256={n:canonical_hash(baseline(load_case(n))) for n in NAMES},source_sha256=sources,code_sha256=code,limits=LIMITS,native_steps_s=list(STEPS),native_timeout_s=180.,native_post_trip_comparison=False)
    if (out/'protocol.json').exists():
        if json.loads((out/'protocol.json').read_text())!=protocol:raise ValueError('protected resume requires identical protocol and producer')
    else:write_json(out/'protocol.json',protocol)
    rows=[];processed=0;write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in NAMES:
            folder=out/name
            if (folder/'comparison.json').exists():
                row=json.loads((folder/'comparison.json').read_text())
                if not row['passed']:raise ValueError('failed protected case requires new output directory')
                for file,h in row['artifacts_sha256'].items():
                    if sha256_file(folder/file)!=h:raise ValueError('protected cached artifact changed')
                rows.append(row);continue
            if processed>=batch_size:break
            spec=load_case(name);unprotected=baseline(spec);write_json(folder/'study.json',spec.model_dump(mode='json'));write_json(folder/'native-unprotected-study.json',unprotected.model_dump(mode='json'));natives=[]
            for h in STEPS:
                print('native pre-trip baseline',name,h,flush=True);n=native_reference(unprotected,h,timeout_s=180.);write_json(folder/f'andes-{h}.json',n)
                if not n.get('completed'):raise ValueError('incomplete native pre-trip baseline')
                natives.append(n)
            primary=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',primary)
            if not primary.get('completed'):raise ValueError('protected primary failed: '+str(primary.get('reason')))
            independent=independent_circuit(spec).run(primary);write_json(folder/'SI.json',independent)
            compared=full_compare(spec,primary,independent);contracts={label:physical_contract(spec,r) for label,r in [('primary',primary),('SI',independent)]};steps=[pretrip_compare(spec,primary,independent,n) for n in natives];native_result=qualify(steps)
            expected_trip=name not in ('equilibrium_hold','manual_open');trip_count=len(trips(primary))
            row=dict(case_id=name,passed=bool(compared['passed'] and all(c['passed'] for c in contracts.values()) and native_result['passed'] and trip_count==int(expected_trip)),full_SI=compared,contracts=contracts,native_pretrip_steps=steps,native_pretrip_result=native_result,trip_count=trip_count,trip_times_s=[e['time_s'] for e in trips(primary)])
            row['artifacts_sha256']={p.name:sha256_file(p) for p in folder.glob('*.json')};write_json(folder/'comparison.json',row);rows.append(row);processed+=1;print('compared',name,row['passed'],flush=True)
            if not row['passed']:raise ValueError('protected case exceeded fixed limits')
        if len(rows)<len(NAMES):summary=dict(passed=False,status='partial',completed_cases=[r['case_id'] for r in rows])
        else:
            rejected=[]
            for name in NEGATIVE:
                spec=load_case(name);p=run_study(spec,isolate=True,wall_timeout_s=90.);reason=None
                try:independent_circuit(spec)
                except ValueError as exc:reason=str(exc)
                passed=not p.get('completed') and reason is not None and (p.get('reason')=='generator_initial_thermal_limit' if name=='initial_limit' else p.get('reason')=='coupled_scope')
                write_json(out/'negative'/name/'production.json',p);rejected.append(dict(case_id=name,passed=bool(passed),reason=reason))
            summary=dict(passed=all(r['passed'] for r in [*rows,*rejected]),status='complete',cases=rows,rejected_cases=rejected,native_unprotected_runs=3*len(rows),full_protected_SI_pairs=len(rows),native_protected_post_trip_runs=0,code_sha256=code,source_sha256=sources,field_validated=False)
            summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        if code!=package_fingerprint() or sources!=source_identity():raise ValueError('protected producer changed')
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--batch-size',type=int,default=2);a=p.parse_args();r=run(a.out,a.batch_size)
    if r['status']=='complete' and not r['passed']:raise SystemExit(1)

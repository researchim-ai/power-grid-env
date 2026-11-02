"""Physical and protection invariants for DOL/dip/coast/restart raw traces."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_restart_cases import NAMES,DIP
from benchmarks.multibus_loaded_audit import current_closure
from benchmarks.native_online_validation import channel_limits
from power_grid_env.report import write_json,sha256_file
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

LIMITS=dict(initial_zero=1e-10,state_jump=1e-10,open_current=1e-10,deadline_s=1e-6,
            coast_speed_pu=1e-6,coast_single_cage_emf_pu=1e-4,physical_KCL_pu=1e-6)


def contract(name,spec,trace,*,native=False):
    if name not in NAMES:raise ValueError('registered restart case required')
    s=spec.coupled if spec.coupled else spec.induction;ch=trace['channels']
    time=np.asarray(trace['time_s'],float);side=trace['sample_side']
    if len(time)<2 or len(side)!=len(time) or not np.isfinite(time).all() or np.any(np.diff(time)<0) or time[0]!=0 or abs(time[-1]-s.duration_s)>1e-12:raise ValueError('invalid restart clock')
    for key in channel_limits(spec):
        values=np.asarray(ch[key],float)
        if values.shape!=time.shape or not np.isfinite(values).all():raise ValueError('invalid restart channel: '+key)
    for m in s.motors:
        if not np.isin(ch[m.id+'.connected'],[0.,1.]).all():raise ValueError('non-binary motor status')
    keys=[m.id+'.'+k for m in s.motors for k in ('speed_pu','e_real_pu','e_imag_pu')]
    keys += [g.id+'.'+k for g in spec.network.generators for k in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_pu','SEXS.field_pu')]
    if any(tag not in ('initial','regular','pre_event','post_event') for tag in side):raise ValueError('unknown restart sample side')
    pairs=[]
    for i,tag in enumerate(side):
        if tag=='pre_event':
            if i+1>=len(time) or side[i+1]!='post_event' or time[i]!=time[i+1]:raise ValueError('unpaired restart event')
            pairs.append((i,i+1))
        elif tag=='post_event' and (i==0 or side[i-1]!='pre_event' or time[i]!=time[i-1]):raise ValueError('orphan post-event sample')
    def at_event(at):
        matches=[(a,b) for a,b in pairs if abs(time[a]-at)<=1e-6]
        if len(matches)!=1:raise ValueError('missing unique event pair')
        return matches[0]
    pre_dip,_=at_event(DIP)
    initial=(time<.1)|((time==.1)&(np.array(side)=='pre_event'))
    initial_zero=max(float(np.max(abs(np.asarray(ch[m.id+'.'+k])[initial]))) for m in s.motors for k in ('current_pu','speed_pu','connected'))
    jump=max((abs(ch[k][b]-ch[k][a]) for a,b in pairs for k in keys),default=0.)
    open_current=max(float(np.max(np.where(np.array(ch[m.id+'.connected'])==0,abs(np.array(ch[m.id+'.current_pu'])),0))) for m in s.motors)
    logs=trace['native_driver_metadata']['event_log'] if native else trace['event_log']
    deadline_errors=[];coast=[];counts={};checks=[]
    for m in s.motors:
        ctrl=m.contactor;events=[e for e in logs if e.get('equipment_id')==m.id];opened=None;available_at=0.;unavailable_at=None;auto=0;uv=0;inhibited=False
        for e in events:
            kind=e['kind'];at=e['time_s'];outcome=e.get('outcome');cause=e.get('cause')
            if kind=='contactor_voltage_unavailable':unavailable_at=at
            elif kind=='contactor_voltage_available':available_at=at
            elif kind=='motor_open' and cause=='undervoltage_timer':
                if unavailable_at is None:raise ValueError('dropout without voltage guard')
                deadline_errors.append(abs(at-unavailable_at-ctrl.dropout_delay_s));uv+=1
                a,b=at_event(at)
                checks += [ch[m.id+'.connected'][a]==1.,ch[m.id+'.connected'][b]==0.,ch[m.id+'.speed_pu'][a]>.5]
                opened=b;inhibited=False
            elif kind=='thermal_trip':
                _,opened=at_event(at);inhibited=True
            elif kind=='motor_open' and outcome in ('applied','already_in_requested_state'):inhibited=True
            elif kind=='motor_close' and cause=='automatic_restart':
                if inhibited or opened is None:raise ValueError('automatic restart without eligible open state')
                deadline_errors.append(abs(at-max(available_at+ctrl.pickup_delay_s,time[opened]+ctrl.minimum_off_time_s)))
                a,b=at_event(at);checks += [ch[m.id+'.connected'][a]==0.,ch[m.id+'.connected'][b]==1.]
                auto+=1
                coast.append(coast_check(m,ch,time,opened,a,spec.network.frequency_hz));opened=None
        if opened is not None:coast.append(coast_check(m,ch,time,opened,len(time)-1,spec.network.frequency_hz))
        blocked=name in ('manual_inhibit','thermal_inhibit') and m.id=='m'
        expect_auto=0 if blocked else 1;expect_uv=0 if name=='thermal_inhibit' and m.id=='m' else 2 if name=='restart_limit' else 1
        counts[m.id]=dict(automatic_restarts=auto,undervoltage_openings=uv)
        checks += [auto==expect_auto,uv==expect_uv,auto<=ctrl.maximum_automatic_restarts,
                   ch[m.id+'.contactor_restart_count'][-1]==auto,ch[m.id+'.contactor_pending_restart'][-1]==0.]
        expected_connected=not blocked and name!='restart_limit'
        checks.append(ch[m.id+'.connected'][-1]==float(expected_connected))
        if not (name=='thermal_inhibit' and m.id=='m'):checks += [ch[m.id+'.speed_pu'][pre_dip]>.95,ch[m.id+'.connected'][pre_dip]==1.]
        if expected_connected:checks.append(ch[m.id+'.speed_pu'][-1]>.95)
    if name=='manual_inhibit':checks.append(any(e['kind']=='motor_close' and e.get('equipment_id')=='m' and e.get('outcome')=='blocked_undervoltage' for e in logs))
    if name=='thermal_inhibit':
        checks += [ch['m.thermal_latched'][-1]==1.,any(e['kind']=='thermal_trip' and e.get('equipment_id')=='m' for e in logs),any(e['kind']=='motor_close' and e.get('equipment_id')=='m' and e.get('outcome')=='blocked_thermal_latch' for e in logs)]
    deadline=max(deadline_errors,default=0.);speed=max((c['speed_error_pu'] for c in coast),default=0.);flux=max((c['emf_error_pu'] for c in coast if c['emf_error_pu'] is not None),default=0.)
    kcl=current_closure(spec,trace) if native or 'node_voltage' in trace else None
    values=dict(initial_zero=initial_zero,state_jump=jump,open_current=open_current,deadline_s=deadline,coast_speed_pu=speed,coast_single_cage_emf_pu=flux,physical_KCL_pu=kcl)
    passed=all(checks) and all(v is None or (math.isfinite(v) and v<=LIMITS[k]) for k,v in values.items())
    return dict(passed=bool(passed),errors=values,counts=counts,coast_segments=coast,checks_passed=bool(all(checks)))


def coast_check(m,ch,time,start,end,frequency):
    duration=time[end]-time[start];w0=ch[m.id+'.speed_pu'][start]
    expected=w0/(1+m.fan_torque_pu/(2*m.inertia_s)*w0*duration)
    flux_error=None
    if m.model=='single_cage':
        mag=lambda i:math.hypot(ch[m.id+'.e_real_pu'][i],ch[m.id+'.e_imag_pu'][i])
        tau=(m.rotor_x_pu+m.magnetizing_x_pu)/(2*math.pi*frequency*m.rotor_r_pu)
        flux_error=abs(mag(end)-mag(start)*math.exp(-duration/tau))
    return dict(motor=m.id,opened_at_s=float(time[start]),end_s=float(time[end]),speed_error_pu=abs(ch[m.id+'.speed_pu'][end]-expected),emf_error_pu=flux_error)


def audit(root):
    from benchmarks.multibus_restart_validation import load_case
    from benchmarks.multibus_motor_audit import check_files
    root=Path(root);read=lambda p:json.loads((root/p).read_text());summary=read('summary.json');protocol=read('protocol.json')
    if not summary['passed'] or not summary['full_registered_corpus'] or protocol['cases']!=list(NAMES):raise ValueError('full passing restart corpus required')
    check_files(root,summary['artifacts_sha256']);check_files(Path(__file__).resolve().parents[1],protocol['source_sha256'])
    if package_fingerprint()!=protocol['code_sha256']:raise ValueError('restart package identity changed')
    rows=[];versions=[];steps=0;rollbacks=0
    for name in NAMES:
        spec=load_case(name)
        if canonical_hash(spec)!=protocol['case_sha256'][name]:raise ValueError('restart case identity changed')
        for file in ['production.json','SI.json',*[f'andes-{h}.json' for h in protocol['native_steps_s']]]:
            trace=read(name+'/'+file);native=file.startswith('andes-');result=contract(name,spec,trace,native=native)
            if not result['passed']:raise ValueError('raw restart contract failed: '+name+'/'+file)
            if native:
                if trace['case_sha256']!=canonical_hash(spec) or trace['code_sha256']!=protocol['code_sha256'] or trace['step_s']!=float(file[6:-5]) or trace['backend_version']!='2.0.0' or trace['controller_commands_precomputed'] or not trace['stock_generated_cache_unchanged']:raise ValueError('native restart provenance failed')
                versions.append(trace['native_source_sha256']);steps+=trace['native_steps_executed'];rollbacks+=trace['native_driver_metadata']['rollback_count']
            rows.append(dict(case=name,file=file,**result))
    if len(versions)!=3*len(NAMES) or any(v!=versions[0] for v in versions):raise ValueError('external backend changed')
    result=dict(passed=True,raw_traces=len(rows),native_runs=len(versions),accepted_native_steps=steps,root_rollbacks=rollbacks,rows=rows,field_validated=False,independent_host=False,summary_sha256=sha256_file(root/'summary.json'))
    write_json(root/'audit.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);a=p.parse_args();audit(a.root)

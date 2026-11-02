"""Independent per-machine controls, continuity, KCL and invariance contracts."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_loaded_audit import current_closure
from benchmarks.native_online_validation import channel_limits,normalized_error
from power_grid_env.report import write_json,sha256_file

LIMITS=dict(state_jump=1e-10,physical_KCL_pu=1e-6,control_reference_delta=1e-12,initial_power_w=.01,initial_Q_agreement_var=.01,metamorphic_normalized=1e-5)


def control_contract(spec,trace,native=False):
    expected=[e for e in spec.coupled.events if e.kind in {'governor_step','voltage_reference_step'}]
    log=trace['native_driver_metadata']['event_log'] if native else trace['event_log']
    observed=[e for e in log if e['kind'] in {'governor_step','voltage_reference_step'}]
    if len(observed)!=len(expected):raise ValueError('missing generator control provenance')
    error=0.
    for event,actual in zip(expected,observed):
        if event.kind!=actual['kind'] or event.equipment_id!=actual['equipment_id'] or abs(event.time_s-actual['time_s'])>1e-12:raise ValueError('wrong generator control event identity')
        before,after=actual['references_before'],actual['references_after'];ids={g.id for g in spec.network.generators}
        if set(before)!=ids or set(after)!=ids:raise ValueError('incomplete per-generator reference inventory')
        for gid in ids:
            for key in ('governor','voltage'):
                wanted=event.value if gid==event.equipment_id and key==('governor' if event.kind=='governor_step' else 'voltage') else 0.
                delta=after[gid][key]-before[gid][key]
                if not math.isfinite(delta):raise ValueError('nonfinite generator reference')
                error=max(error,abs(delta-wanted))
    return dict(passed=error<=LIMITS['control_reference_delta'],maximum_delta_error=error,events=len(observed))


def trace_contract(spec,trace,native=False):
    ch=trace['channels'];times=np.asarray(trace['time_s']);sides=trace['sample_side']
    if len(times)<2 or not np.isfinite(times).all() or np.any(np.diff(times)<0):raise ValueError('invalid multigen clock')
    for key in channel_limits(spec):
        a=np.asarray(ch[key]);
        if a.shape!=times.shape or not np.isfinite(a).all():raise ValueError('invalid multigen channel: '+key)
    keys=[g.id+'.'+k for g in spec.network.generators for k in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_pu','SEXS.field_pu')]
    keys += [m.id+'.'+k for m in spec.coupled.motors for k in ('speed_pu','e_real_pu','e_imag_pu')]
    jumps=[]
    for i,side in enumerate(sides):
        if side=='pre_event':
            if i+1>=len(times) or sides[i+1]!='post_event' or times[i]!=times[i+1]:raise ValueError('unpaired multigen event')
            jumps.extend(abs(ch[k][i+1]-ch[k][i]) for k in keys)
    jump=max(jumps,default=0.);p_error=max(abs(ch[g.id+'.p_w'][0]-g.p_w) for g in spec.network.generators)
    closure=current_closure(spec,trace) if native or 'node_voltage' in trace else None
    controls=control_contract(spec,trace,native) if native or trace.get('independent_SI_network') else None
    passed=jump<=LIMITS['state_jump'] and p_error<=LIMITS['initial_power_w'] and (closure is None or closure<=LIMITS['physical_KCL_pu']) and (controls is None or controls['passed'])
    return dict(passed=bool(passed),maximum_state_jump=jump,maximum_initial_P_error_w=p_error,maximum_physical_KCL_pu=closure,controls=controls)


def invariant(spec,left,right):
    if left['time_s']!=right['time_s'] or left['sample_side']!=right['sample_side']:raise ValueError('metamorphic comparison clocks differ')
    limits=channel_limits(spec);errors={k:float(np.max(abs(np.array(left['channels'][k])-right['channels'][k]))) for k in limits}
    score=normalized_error(errors,limits)
    return dict(passed=score<=LIMITS['metamorphic_normalized'],maximum_normalized_error=score,errors=errors)


def audit(root):
    from benchmarks.multigen_validation import load_case
    from benchmarks.multigen_cases import NAMES,NEGATIVE
    from benchmarks.multibus_motor_audit import check_files
    root=Path(root);read=lambda p:json.loads((root/p).read_text());summary=read('summary.json');protocol=read('protocol.json')
    if not summary['passed'] or summary['status']!='complete' or protocol['cases']!=list(NAMES) or len(summary['rejected_cases'])!=len(NEGATIVE):raise ValueError('full passing multigen corpus required')
    check_files(root,summary['artifacts_sha256']);check_files(Path(__file__).resolve().parents[1],protocol['source_sha256'])
    from power_grid_env.evidence import package_fingerprint
    if package_fingerprint()!=protocol['code_sha256']:raise ValueError('multigen core identity changed')
    rows=[];native_sources=[];steps=0
    for name in NAMES:
        spec=load_case(name)
        for file in ['production.json','SI.json',*[f'andes-{h}.json' for h in protocol['native_steps_s']]]:
            r=read(name+'/'+file);native=file.startswith('andes-');result=trace_contract(spec,r,native)
            if not result['passed']:raise ValueError('multigen raw contract failed: '+name+'/'+file)
            if native:
                from power_grid_env.model import canonical_hash
                if not r['completed'] or r['case_sha256']!=canonical_hash(spec) or r['code_sha256']!=protocol['code_sha256'] or r['step_s']!=float(file[6:-5]) or not r['stock_generated_cache_unchanged'] or r['controller_commands_precomputed']:raise ValueError('multigen native provenance failed')
                native_sources.append(r['native_source_sha256']);steps+=r['native_steps_executed']
            rows.append(dict(case=name,file=file,**result))
    if len(native_sources)!=3*len(NAMES) or any(p!=native_sources[0] for p in native_sources):raise ValueError('external backend changed')
    result=dict(passed=True,raw_traces=len(rows),native_runs=len(native_sources),accepted_native_steps=steps,rows=rows,field_validated=False,independent_host=False)
    write_json(root/'audit.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);a=p.parse_args();audit(a.root)

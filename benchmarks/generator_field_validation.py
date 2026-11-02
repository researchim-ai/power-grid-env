"""Full independent SI and matrix-exponential post-quench rotor/heat oracle."""
from pathlib import Path
import json,argparse,math
import numpy as np
from scipy.linalg import expm
from benchmarks.generator_field_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.generator_thermal_validation import full_compare,physical_contract
from power_grid_env.study_spec import run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file


def field_trips(report):return [e for e in report['event_log'] if e['kind']=='generator_field_thermal_trip']


def analytic_quench(spec,report):
    errors={}
    for p in spec.coupled.generator_field_thermal:
        gid=p.generator_id;m=next(m for m in spec.coupled.machines if m.generator_id==gid)
        if m.saturation!='disabled':continue
        trips=[e for e in field_trips(report) if e['equipment_id']==gid]
        if not trips:continue
        at=trips[0]['time_s'];times=np.asarray(report['time_s']);ch=report['channels']
        index=next(i for i,(t,side) in enumerate(zip(times,report['sample_side'])) if t==at and side=='post_event')
        coefficient=(m.x_d_pu-m.x_d_prime_pu)*(m.x_d_prime_pu-m.x_d_subtransient_pu)/(m.x_d_prime_pu-m.leakage_pu)**2
        c=np.array([1+coefficient,-coefficient]);A=np.array([[-c[0]/m.td_prime_s,-c[1]/m.td_prime_s],[1/m.td_subtransient_s,-1/m.td_subtransient_s]])
        M=np.zeros((5,5));M[:4,:4]=np.kron(A,np.eye(2))+np.kron(np.eye(2),A)
        M[4,:4]=np.kron(c,c)/(p.time_constant_s*p.rated_field_reaction_pu**2);M[4,4]=-1/p.time_constant_s
        q=np.array([ch[gid+'.e1q'][index],ch[gid+'.e2d'][index]])
        start=np.r_[np.kron(q,q),ch[gid+'.field_thermal_level'][index]]
        heat_error=0.;current_error=0.
        for j in range(index,len(times)):
            dt=times[j]-at
            expected=expm(M*dt)@start;current=abs(c@(expm(A*dt)@q))/p.rated_field_reaction_pu
            heat_error=max(heat_error,abs(ch[gid+'.field_thermal_level'][j]-expected[-1]))
            current_error=max(current_error,abs(ch[gid+'.field_current_pu'][j]-current))
        errors[gid]=dict(heat=heat_error,current=current_error)
    return dict(passed=all(max(v.values())<=1e-7 for v in errors.values()),errors=errors)


def compare(spec,primary,si):
    checks=full_compare(spec,primary,si);physical=physical_contract(spec,primary)
    left,right=field_trips(primary),field_trips(si)
    if [e['equipment_id'] for e in left]!=[e['equipment_id'] for e in right]:raise ValueError('field protection trip sequence differs')
    delta=max((abs(a['time_s']-b['time_s']) for a,b in zip(left,right)),default=0.)
    analytic=analytic_quench(spec,primary);continuous=[];actuator=True
    for i,side in enumerate(primary['sample_side']):
        if side!='pre_event':continue
        for p in spec.coupled.generator_field_thermal:continuous.append(abs(primary['channels'][p.generator_id+'.field_thermal_level'][i+1]-primary['channels'][p.generator_id+'.field_thermal_level'][i]))
    for e in left:
        gid=e['equipment_id'];ch=primary['channels'];off=np.asarray(primary['time_s'])>=e['time_s']
        off&=np.array(primary['sample_side'])!='pre_event'
        actuator &= bool(np.all(np.asarray(ch[gid+'.field_voltage_enabled'])[off]==0) and np.all(np.asarray(ch[gid+'.field_pu'])[off]==0) and np.all(np.asarray(ch[gid+'.current_pu'])[off]==0))
        actuator &= max(np.asarray(ch[gid+'.field_current_pu'])[off])>.01
    return dict(passed=bool(checks['passed'] and physical['passed'] and analytic['passed'] and actuator and delta<=1e-6 and max(continuous,default=0.)<=1e-10),SI=checks,physical=physical,analytic=analytic,maximum_trip_time_error_s=delta,maximum_heat_state_jump=max(continuous,default=0.),field_current_memory_preserved=bool(actuator))


def run(out):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'protocol.json').exists():raise ValueError('use a new field validation output directory')
    protocol=dict(code_sha256=package_fingerprint(),cases={n:canonical_hash(example(n)) for n in NAMES},limits=dict(SI_state=1e-5,heat_matrix_exponential=1e-7,trip_time_s=1e-6),native_field_thermal_runs=0)
    write_json(out/'protocol.json',protocol);write_json(out/'summary.json',dict(passed=False,status='running'))
    checks=[]
    for name in NAMES:
        print(name,flush=True);s=example(name);r=run_study(s,isolate=True,wall_timeout_s=90.)
        write_json(out/name/'study.json',s.model_dump(mode='json'));write_json(out/name/'production.json',r)
        if not r.get('completed'):raise ValueError('incomplete primary '+name)
        i=independent_circuit(s).run(r);write_json(out/name/'SI.json',i)
        c=compare(s,r,i);write_json(out/name/'comparison.json',c)
        if not c['passed']:raise ValueError('field comparison failed '+name)
        checks.append(c)
    if package_fingerprint()!=protocol['code_sha256']:raise ValueError('producer changed during field validation')
    result=dict(passed=True,cases=len(checks),native_field_thermal_runs=0,maximum_trip_time_error_s=max(c['maximum_trip_time_error_s'] for c in checks),maximum_matrix_heat_error=max(v['heat'] for c in checks for v in c['analytic']['errors'].values()),code_sha256=protocol['code_sha256'],artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*.json') if p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args();print(run(args.out))

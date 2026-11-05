"""Registered analytic-vs-FD GENCLS corpus, measured in fresh bounded workers.

FD uses exactly the same physical RHS, so trajectory agreement is an algorithm
regression, not an independent physical reference. Six simple cases separately
use the independent SI winding implementation in test_coupled_jacobian.py.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_jacobian import KIND
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity

NAMES=('hold','motor_load_step','generator_step','voltage_sag','cold_start','thermal_trip','double_cage_start','generator_thermal_trip','scale_64_4_1','scale_512_32_4','scale_2000_8_4')
STATE_LIMIT=2e-5
POWER_LIMIT=100.
EVENT_LIMIT=1e-6


def case(name):
    if name not in NAMES:raise ValueError('unregistered Jacobian case')
    if name.startswith('scale_'):
        from benchmarks.ex03_cases import scale_case
        from power_grid_env.rms_study import ClassicalMachine
        s=scale_case(*map(int,name.split('_')[1:]))
        s.coupled.machines=[ClassicalMachine(generator_id=m.generator_id,rated_va=m.rated_va,inertia_s=m.inertia_s,x_d_prime_pu=m.x_d_prime_pu,damping_pu=m.damping_pu) for m in s.coupled.machines]
    else:
        base='cold_start' if name=='double_cage_start' else 'generator_step' if name=='generator_thermal_trip' else name
        s=StudySpec.model_validate_json(Path('cases/studies/coupled_'+base+'.json').read_text())
        if name=='double_cage_start':
            m=s.coupled.motors[0];m.model='double_cage';m.rotor2_r_pu=.07;m.rotor2_x_pu=.12
        if name=='generator_thermal_trip':
            from power_grid_env.generator_thermal import GeneratorThermalProtection
            s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g',time_constant_s=.05,trip_level=.003)]
    s.id='analytic-gencls-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def compare(a,b):
    if not a.get('completed') or not b.get('completed'):raise ValueError('both runs must complete')
    if a.get('sample_side')!=b.get('sample_side') or set(a['channels'])!=set(b['channels']):raise ValueError('trace inventory differs')
    ta=np.asarray(a['time_s']);tb=np.asarray(b['time_s'])
    if ta.shape!=tb.shape or not np.isfinite(ta).all() or not np.isfinite(tb).all():raise ValueError('invalid sample axis')
    if not a['channels'] or not len(ta):raise ValueError('empty trace cannot qualify')
    errors={}
    for k in a['channels']:
        x=np.asarray(a['channels'][k]);y=np.asarray(b['channels'][k])
        if x.shape!=ta.shape or y.shape!=tb.shape or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('invalid channel: '+k)
        errors[k]=float(max(abs(x-y)))
    events=lambda r:[tuple(x.get(k) for k in ('kind','equipment_id','outcome','controller','bound','port','gate','active','transition'))+(x.get('value') if type(x.get('value')) is bool else None,) for x in r['event_log']]
    if events(a)!=events(b):raise ValueError('discrete event sequence differs')
    clocks=max([0.,*[abs(x['time_s']-y['time_s']) for x,y in zip(a['event_log'],b['event_log'])]])
    axis=float(max(abs(ta-tb)))
    passed=axis<=EVENT_LIMIT and clocks<=EVENT_LIMIT and all(v<=(POWER_LIMIT if k.endswith(('_w','_var')) else STATE_LIMIT) for k,v in errors.items())
    for r in (a,b):passed=passed and r['maximum_kcl_residual_pu']<=1e-8 and r['maximum_power_balance_residual_pu']<=1e-8
    return dict(passed=bool(passed),maximum_axis_error_s=axis,maximum_event_error_s=clocks,channel_errors=errors)


def worker(study,mode,out,*,analytic_kind=KIND):
    spec=StudySpec.model_validate_json(Path(study).read_text());out=Path(out);out.mkdir(parents=True,exist_ok=False)
    calls=0;electrical_calls=0;rhs=CoupledNetwork.rhs;electrical=CoupledNetwork.electrical
    def counted_rhs(self,*args):
        nonlocal calls;calls+=1;return rhs(self,*args)
    def counted_electrical(self,*args):
        nonlocal electrical_calls;electrical_calls+=1;return electrical(self,*args)
    wall=time.perf_counter();cpu=time.process_time()
    with patch.object(CoupledNetwork,'rhs',counted_rhs),patch.object(CoupledNetwork,'electrical',counted_electrical):
        if mode=='fd':
            with patch.object(CoupledNetwork,'integration_jacobian',lambda self:None):result=run_study(spec)
        elif mode=='analytic':result=run_study(spec)
        else:raise ValueError('unknown solver mode')
    metrics=dict(completed=bool(result.get('completed')),mode=mode,actual_rhs_calls=calls,electrical_calls=electrical_calls,
                 wall_s=time.perf_counter()-wall,cpu_s=time.process_time()-cpu,code_sha256=package_fingerprint(),case_sha256=canonical_hash(spec),integration=result.get('integration'))
    (out/'trace.json').write_text(json.dumps(result,allow_nan=False)+'\n');(out/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    if not result.get('completed'):raise ValueError('run failed: '+str(result))
    expected=analytic_kind if mode=='analytic' else 'scipy_finite_difference'
    if result['integration']['jacobian']!=expected:raise ValueError('solver path differs from declared protocol')


def qualify(out,repeats=2,*,names=NAMES,case_factory=case,worker_module='benchmarks.coupled_jacobian_validation'):
    if not names or len(set(names))!=len(names):raise ValueError('nonempty unique case inventory required')
    if type(repeats) is not int or not 2<=repeats<=5:raise ValueError('two to five repeats required')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    code=package_fingerprint();sources=source_identity();specs={n:case_factory(n) for n in names}
    protocol=dict(code_sha256=code,source_sha256=sources,cases={n:s.model_dump(mode='json') for n,s in specs.items()},repeats=repeats,
                  limits=dict(state=STATE_LIMIT,power_w_var=POWER_LIMIT,event_s=EVENT_LIMIT,balance_pu=1e-8),power_channel_suffixes=['_w','_var'],worker_timeout_s=120,
                  comparison='same physical RHS, analytic vs finite difference Jacobian; not independent physics',speedup_required=False)
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    rows=[]
    try:
        for name,s in specs.items():
            target=out/name;target.mkdir();study=target/'study.json';study.write_text(s.model_dump_json())
            for repeat in range(repeats):
                metrics={};traces={}
                for mode in ('analytic','fd'):
                    folder=target/f'{mode}-{repeat}'
                    cmd=[sys.executable,'-m',worker_module,'--child','--study',str(study),'--mode',mode,'--out',str(folder)]
                    env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
                    r=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=120)
                    (target/f'{mode}-{repeat}.log').write_text(r.stdout+'\n'+r.stderr)
                    if r.returncode:raise ValueError('worker failed: '+name+' '+mode)
                    metrics[mode]=json.loads((folder/'metrics.json').read_text());traces[mode]=json.loads((folder/'trace.json').read_text())
                    if metrics[mode]['code_sha256']!=code or metrics[mode]['case_sha256']!=canonical_hash(s):raise ValueError('worker producer/input differs')
                check=compare(traces['analytic'],traces['fd']);rows.append(dict(case=name,repeat=repeat,comparison=check,metrics=metrics))
                if not check['passed']:raise ValueError('trajectory comparison failed: '+name)
                print(name,repeat,'passed',flush=True)
        if package_fingerprint()!=code or source_identity()!=sources:raise ValueError('producer changed during qualification')
        result=dict(passed=True,status='complete',cases=rows,code_sha256=code,source_sha256=sources,independent_host_verified=False,industrial_profile_qualified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',cases=rows,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n')
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:worker(a.study,a.mode,a.out)
    else:qualify(a.out)


if __name__=='__main__':main()

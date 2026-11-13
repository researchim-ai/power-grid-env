"""PHY-19 bounded full-circuit electrothermal comparison; no relaxed legacy caps."""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile
import numpy as np
from benchmarks.continuous_drive_acceptance import document as base, REFERENCES
from benchmarks.continuous_drive_reference import VERSION, WHEEL_SHA256
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

NAMES=('warm_device_losses','switching_trip','conduction_trip','winding_trip','brake_trip','rectifier_trip','lossy_block_recovery')
FRACTIONS=(.05,.0125)
HEAT_CAP_J=1e-5
HEAT_KEYS=('switching_heat_j','motor_stator_heat_j','motor_rotor_heat_j','motor_core_heat_j')


def document(name):
    if name not in NAMES:raise ValueError('unknown electrothermal case')
    p=base('warm_core_start');p.update(id='electrothermal-'+name,duration_s=.004,grid_envelope=[(0.,1.)],gate_block_windows=[])
    p.update(switching_on_j=.005,switching_off_j=.003,switching_temperature_per_k=.002)
    p['thermal'].update(resistance_alpha_per_k=.004,initial_c=[65.,75.,50.],trip_c=[140.]*3)
    if name=='switching_trip':
        p['thermal'].update(capacity_j_per_k=[40.,.05,50.],trip_c=[140.,75.001,140.])
    if name=='conduction_trip':
        p.update(switching_on_j=0.,switching_off_j=0.)
        p['thermal'].update(capacity_j_per_k=[40.,.05,50.],conductance_w_per_k=[2.,.001,1.],trip_c=[140.,75.01,140.])
    if name=='winding_trip':
        p['motor']['thermal'].update(stator_capacity_j_per_k=.05,stator_ambient_w_per_k=.001,cross_w_per_k=.001)
        p['motor_temperature_trip_c']=[90.1,140.]
    if name=='brake_trip':
        p.update(brake_start_v=500.,brake_full_v=550.)
        p['thermal'].update(capacity_j_per_k=[40.,30.,.05],conductance_w_per_k=[2.,2.,.001],trip_c=[140.,140.,50.1])
    if name=='rectifier_trip':
        p['initial_dc_v']=300.
        p['thermal'].update(capacity_j_per_k=[.05,30.,50.],conductance_w_per_k=[.001,2.,1.],trip_c=[65.1,140.,140.])
    if name=='lossy_block_recovery':
        p.update(duration_s=.012,gate_block_windows=[(.004,.006)])
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')


def producer():
    names=['electrothermal_drive_acceptance.py','electrothermal_drive_reference.py','continuous_drive_reference.py','continuous_drive_acceptance.py','extended_closed_loop_acceptance.py','switching_thermal_cases.py','switching_extended_cases.py','switching_drive_cases.py']
    return dict(package=package_fingerprint(),benchmarks={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names})


def features(name,result):
    events=result['event_log'];trips=[e for e in events if e['kind']=='thermal_trip']
    required={'switching_trip':('inverter','switching_energy'),'conduction_trip':('inverter',None),'winding_trip':('motor_stator',None),'brake_trip':('brake',None),'rectifier_trip':('rectifier',None)}
    checks=dict(completed=bool(result['completed']))
    if name in required:
        component,cause=required[name]
        checks['required_trip']=any(e['component']==component and e.get('cause')==cause for e in trips)
        checks['latched_at_end']=bool(result['channels']['thermal_trip'][-1])
        checks['gates_blocked_at_end']=not bool(result['channels']['gates_enabled'][-1])
    else:
        checks['no_trip']=not trips
        checks['switching_dissipation']=result['metrics'].get('switching_heat_j',0)>0
    if name=='lossy_block_recovery':
        times=np.asarray(result['time_s']);gates=np.asarray(result['channels']['gates_enabled'])
        blocked=(times>.004)&(times<.006);recovered=(times>.008)&(times<.012)
        checks['block_and_recovery']=bool(any(blocked) and any(recovered) and not any(gates[blocked]) and all(gates[recovered]))
    return checks


def heat_check(a,b):
    errors={key:abs(a['metrics'][key]-b['metrics'][key]) for key in HEAT_KEYS}
    if not all(np.isfinite(v) for v in errors.values()):raise ValueError('nonfinite heat comparison')
    return dict(passed=all(v<=HEAT_CAP_J for v in errors.values()),errors_j=errors,cap_j=HEAT_CAP_J)


def assess(name,p,refs,primaries):
    study=SwitchingDriveStudy.model_validate(p);limits=SwitchingTemporalLimits()
    if len(refs)!=2 or len(primaries)!=len(FRACTIONS):raise ValueError('complete reference/primary inventory required')
    agreement=differences(study,*refs,limits);reference_audits=[audited(study,r,limits) for r in refs]
    reference_features=[features(name,r) for r in refs];reference_heat=heat_check(*refs)
    reference_passed=agreement['passed'] and all(g['maximum_normalized']<=.01 for g in agreement['groups'].values()) and all(all(a.values()) for a in reference_audits+reference_features) and reference_heat['passed'] and all(r['metrics']['maximum_energy_relative_error']<1e-8 and r['metrics']['maximum_switch_energy_residual_j']<1e-8 for r in refs)
    checks=[]
    for fraction,result in zip(FRACTIONS,primaries):
        audit=audited(study,result,limits);physical=features(name,result)
        comparisons=[differences(study,result,r,limits) for r in refs];heats=[heat_check(result,r) for r in refs]
        checks.append(dict(fraction=fraction,passed=all(audit.values()) and all(physical.values()) and all(c['passed'] for c in comparisons+heats),audits=audit,features=physical,comparisons=comparisons,heat_comparisons=heats))
    return dict(case=name,passed=reference_passed and all(c['passed'] for c in checks),reference_passed=reference_passed,reference_agreement=agreement,reference_audits=reference_audits,reference_features=reference_features,reference_heat=reference_heat,primary_checks=checks)


def native_sources(wheel):
    if hashlib.sha256(Path(wheel).read_bytes()).hexdigest()!=WHEEL_SHA256:raise ValueError('native wheel differs')
    with zipfile.ZipFile(wheel) as archive:
        return {n:hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if n.startswith('motulator/') and n.endswith('.py')}


def check_reference(result,settings,identity):
    if result['native_source_sha256']!=identity or result['reference']['machine_version']!=VERSION or not result['reference']['production_imports_blocked']:raise ValueError('reference identity/isolation differs')
    for key,value in settings.items():
        if result['reference']['max_step_s' if key=='max_step' else key]!=value:raise ValueError('reference solver controls differ')


def run(out,python,wheel,names=NAMES):
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('unique registered cases required')
    identity=native_sources(wheel)
    before=producer();out=Path(out);out.mkdir(parents=True,exist_ok=False);limits=SwitchingTemporalLimits()
    (out/'protocol.json').write_bytes(encoded(dict(cases=names,fractions=FRACTIONS,references=REFERENCES,limits=limits.model_dump(mode='json'),reference_uncertainty_fraction=.01,heat_cap_j=HEAT_CAP_J,native_version=VERSION,native_wheel_sha256=WHEEL_SHA256,producer=before)))
    rows=[]
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',MPLCONFIGDIR='/tmp/pge-continuous-mpl')
    for name in names:
        folder=out/name;folder.mkdir();p=document(name);study=SwitchingDriveStudy.model_validate(p)
        (folder/'study.json').write_bytes(encoded(p));refs=[]
        try:
            for i,settings in enumerate(REFERENCES):
                command=[str(python),'-m','benchmarks.electrothermal_drive_reference','--study',str(folder/'study.json'),'--wheel',str(wheel),'--out',str(folder/f'reference-{i}.json'),'--method',settings['method'],'--rtol',str(settings['rtol']),'--atol',str(settings['atol']),'--max-step',str(settings['max_step'])]
                worker=subprocess.run(command,env=env,capture_output=True,text=True,timeout=600)
                (folder/f'reference-{i}.log').write_text(worker.stdout+worker.stderr)
                if worker.returncode:raise RuntimeError('independent worker failed: '+worker.stderr[-2500:])
                r=read_json(folder/f'reference-{i}.json')
                check_reference(r,settings,identity)
                refs.append(r)
            primaries=[]
            for i,fraction in enumerate(FRACTIONS):
                candidate=copy.deepcopy(p);candidate['adaptive']={'error_budget_fraction':fraction}
                (folder/f'primary-{i}-study.json').write_bytes(encoded(SwitchingDriveStudy.model_validate(candidate).model_dump(mode='json')))
                result=dict(run_switching_drive(candidate),code_sha256=before['package']);(folder/f'primary-{i}.json').write_bytes(encoded(result))
                primaries.append(result)
            row=assess(name,p,refs,primaries)
            for c in row['primary_checks']:print(name,c['fraction'],c['passed'],flush=True)
        except (ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
            row=dict(case=name,passed=False,error=str(exc));print(name,'failed',str(exc),flush=True)
        (folder/'assessment.json').write_bytes(encoded(row));rows.append(row)
    if producer()!=before:raise RuntimeError('comparison sources changed during execution')
    result=dict(schema='power-grid-env.electrothermal-drive-comparison/v1',passed=all(r['passed'] for r in rows),rows=rows,scope='seven authored single-cage positive-Gc profiles, 4ms trips and 12ms block/recovery; not full multicage/OEM qualification',producer=before)
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


def verify_result(root,wheel):
    """Recompute every verdict, not merely the untrusted stored summary flag."""
    root=Path(root);manifest=verify(root);protocol=read_json(root/'protocol.json');identity=native_sources(wheel)
    names=protocol['cases']
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique cases required')
    expected=dict(cases=names,fractions=list(FRACTIONS),references=list(REFERENCES),limits=SwitchingTemporalLimits().model_dump(mode='json'),reference_uncertainty_fraction=.01,heat_cap_j=HEAT_CAP_J,native_version=VERSION,native_wheel_sha256=WHEEL_SHA256,producer=producer())
    if protocol!=expected:raise ValueError('protocol or current producer differs')
    rows=[]
    for name in names:
        folder=root/name;p=document(name)
        if read_json(folder/'study.json')!=p:raise ValueError('registered study differs')
        refs=[read_json(folder/f'reference-{i}.json') for i in range(2)]
        for r,settings in zip(refs,REFERENCES):check_reference(r,settings,identity)
        primaries=[]
        for i,fraction in enumerate(FRACTIONS):
            candidate=copy.deepcopy(p);candidate['adaptive']={'error_budget_fraction':fraction}
            if read_json(folder/f'primary-{i}-study.json')!=SwitchingDriveStudy.model_validate(candidate).model_dump(mode='json'):raise ValueError('primary study differs')
            result=read_json(folder/f'primary-{i}.json')
            if result['code_sha256']!=protocol['producer']['package']:raise ValueError('primary producer differs')
            primaries.append(result)
        row=assess(name,p,refs,primaries)
        if row!=read_json(folder/'assessment.json'):raise ValueError('stored assessment differs from raw trajectories')
        rows.append(row)
    summary=read_json(root/'summary.json')
    if summary['rows']!=rows or summary['passed']!=all(r['passed'] for r in rows) or summary['producer']!=producer():raise ValueError('stored summary differs')
    if verify(root)!=manifest:raise ValueError('evidence changed during verification')
    return dict(verified=True,passed=summary['passed'],cases=len(rows),manifest_sha256=manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out');parser.add_argument('--python');parser.add_argument('--wheel',required=True);parser.add_argument('--case',action='append',choices=NAMES);parser.add_argument('--verify')
    a=parser.parse_args()
    if a.verify:r=verify_result(a.verify,a.wheel);print(r)
    else:
        if not a.out or not a.python:parser.error('--out and --python required for a new run')
        r=run(a.out,a.python,a.wheel,tuple(a.case) if a.case else NAMES)
    raise SystemExit(0 if r['passed'] else 2)

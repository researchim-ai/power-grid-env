"""PHY-20 bounded independent full continuous multicage-drive qualification."""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import numpy as np
from benchmarks.electrothermal_drive_acceptance import document as base,producer as base_producer,heat_check,HEAT_CAP_J
from benchmarks.continuous_drive_acceptance import REFERENCES
from benchmarks.multicage_drive_reference import reference_sources
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

NAMES=('double_linear','double_saturated_warm','triple_saturated_warm','four_saturated_warm','four_recovery','four_rotor_trip')
FRACTIONS=(.05,.0125)


def document(name):
    if name not in NAMES:raise ValueError('unregistered multicage profile')
    p=base('warm_device_losses');p['id']='multicage-'+name;m=p['motor']
    count=2 if name.startswith('double') else 3 if name.startswith('triple') else 4
    for k in range(count-1):m['cages'].append(dict(resistance_ohm=5.+k,leakage_h=.018+.004*k))
    p['motor_initial_fluxes_ab_vs']=[[.6,0.]]+[[.58,0.] for _ in range(count)]
    m['provenance']['extra_cages']='Authored passive parallel Gamma rotor branches R=5+k ohm, L=0.018+0.004k H; no measured skin-depth identification'
    if name=='double_linear':m['saturation_beta_per_vs']=0.
    nominal=p['observer_motor']
    nominal['cages']=[dict(resistance_ohm=1/sum(1/c['resistance_ohm'] for c in m['cages']),leakage_h=1/sum(1/c['leakage_h'] for c in m['cages']))]
    nominal['provenance']['electrical']='Authored parallel-R/parallel-L nominal observer; not an exact frequency-dependent reduction of unequal rotor time constants'
    ratio=nominal['cages'][0]['leakage_h']/m['cages'][0]['leakage_h']
    p['control']['current_kp_ohm']*=ratio;p['control']['current_ki_ohm_per_s']*=ratio
    if name=='four_recovery':
        p.update(duration_s=.012,gate_block_windows=[(.004,.006)],grid_envelope=[(0.,1.),(.002,1.),(.003,.7),(.006,.7),(.008,1.)])
    if name=='four_rotor_trip':
        m['thermal'].update(rotor_capacity_j_per_k=.05,rotor_ambient_w_per_k=.001,cross_w_per_k=.001)
        p['motor_temperature_trip_c']=[140.,70.05]
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')


def producer():
    p=base_producer()
    for name in ['multicage_drive_reference.py','multicage_drive_acceptance.py']:
        p['benchmarks'][name]=hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    return p


def protocol(names):
    return dict(cases=list(names),fractions=list(FRACTIONS),references=list(REFERENCES),limits=SwitchingTemporalLimits().model_dump(mode='json'),reference_uncertainty_fraction=.01,heat_cap_j=HEAT_CAP_J,reference_source_sha256=reference_sources(),producer=producer())


def features(name,result):
    trips=[e for e in result['event_log'] if e['kind']=='thermal_trip']
    checks=dict(completed=bool(result['completed']),positive_switch_heat=result['metrics']['switching_heat_j']>0,positive_core_heat=result['metrics']['motor_core_heat_j']>0,positive_rotor_heat=result['metrics']['motor_rotor_heat_j']>0)
    if name=='four_rotor_trip':
        checks['rotor_trip']=any(e['component']=='motor_rotor' and e.get('cause') is None for e in trips)
        checks['latched']=bool(result['channels']['motor_rotor_thermal_trip'][-1])
        checks['gates_blocked']=not bool(result['channels']['gates_enabled'][-1])
    else:checks['no_trip']=not trips
    if name=='four_recovery':
        t=np.asarray(result['time_s']);gates=np.asarray(result['channels']['gates_enabled'])
        blocked=(t>.004)&(t<.006);recovered=(t>.009)&(t<.012)
        checks['block_and_recovery']=bool(any(blocked) and any(recovered) and not any(gates[blocked]) and all(gates[recovered]))
    return checks


def check_reference(r,settings):
    if r.get('reference_source_sha256')!=reference_sources() or not r['reference']['production_imports_blocked'] or r['reference'].get('machine_model')!='independent_all_flux_Gamma' or r['reference'].get('external_machine') is not False:raise ValueError('independent reference identity/scope differs')
    for key,value in settings.items():
        if r['reference']['max_step_s' if key=='max_step' else key]!=value:raise ValueError('reference solver controls differ')


def assess(name,p,refs,primaries):
    study=SwitchingDriveStudy.model_validate(p);limits=SwitchingTemporalLimits()
    if len(refs)!=2 or len(primaries)!=len(FRACTIONS):raise ValueError('complete trajectory inventory required')
    agreement=differences(study,*refs,limits);ref_audits=[audited(study,r,limits) for r in refs];ref_features=[features(name,r) for r in refs]
    cage_checks=[]
    for r in refs:
        heat=np.asarray(r['metrics']['rotor_cage_heat_j'])
        cage_checks.append(bool(heat.shape==(len(p['motor']['cages']),) and np.isfinite(heat).all() and np.all(heat>0) and abs(sum(heat)-r['metrics']['motor_rotor_heat_j'])<1e-9))
    ref_passed=agreement['passed'] and all(g['maximum_normalized']<=.01 for g in agreement['groups'].values()) and all(all(a.values()) for a in ref_audits+ref_features) and all(cage_checks) and heat_check(*refs)['passed'] and all(r['metrics']['maximum_energy_relative_error']<1e-8 and r['metrics']['maximum_switch_energy_residual_j']<1e-8 for r in refs)
    checks=[]
    for fraction,result in zip(FRACTIONS,primaries):
        a=audited(study,result,limits);f=features(name,result);comparisons=[differences(study,result,r,limits) for r in refs];heats=[heat_check(result,r) for r in refs]
        checks.append(dict(fraction=fraction,passed=all(a.values()) and all(f.values()) and all(v['passed'] for v in comparisons+heats),audits=a,features=f,comparisons=comparisons,heat_comparisons=heats))
    return dict(case=name,passed=ref_passed and all(c['passed'] for c in checks),reference_passed=ref_passed,reference_agreement=agreement,reference_audits=ref_audits,reference_features=ref_features,reference_cage_heat_checks=cage_checks,primary_checks=checks)


def run(out,python,names=NAMES):
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('unique registered cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);before=protocol(names);(out/'protocol.json').write_bytes(encoded(before));rows=[]
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    for name in names:
        folder=out/name;folder.mkdir();p=document(name);(folder/'study.json').write_bytes(encoded(p));refs=[]
        try:
            for i,settings in enumerate(REFERENCES):
                command=[str(python),'-m','benchmarks.multicage_drive_reference','--study',str(folder/'study.json'),'--out',str(folder/f'reference-{i}.json'),'--method',settings['method'],'--rtol',str(settings['rtol']),'--atol',str(settings['atol']),'--max-step',str(settings['max_step'])]
                worker=subprocess.run(command,env=env,capture_output=True,text=True,timeout=600)
                (folder/f'reference-{i}.log').write_text(worker.stdout+worker.stderr)
                if worker.returncode:raise RuntimeError('independent worker failed: '+worker.stderr[-2000:])
                r=read_json(folder/f'reference-{i}.json');check_reference(r,settings);refs.append(r)
            primaries=[]
            for i,fraction in enumerate(FRACTIONS):
                candidate=copy.deepcopy(p);candidate['adaptive']={'error_budget_fraction':fraction}
                (folder/f'primary-{i}-study.json').write_bytes(encoded(SwitchingDriveStudy.model_validate(candidate).model_dump(mode='json')))
                r=dict(run_switching_drive(candidate),code_sha256=before['producer']['package']);(folder/f'primary-{i}.json').write_bytes(encoded(r));primaries.append(r)
            row=assess(name,p,refs,primaries)
            for c in row['primary_checks']:print(name,c['fraction'],c['passed'],flush=True)
        except (ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
            row=dict(case=name,passed=False,error=str(exc));print(name,'failed',str(exc),flush=True)
        (folder/'assessment.json').write_bytes(encoded(row));rows.append(row)
    if protocol(names)!=before:raise RuntimeError('multicage producer changed during execution')
    result=dict(schema='power-grid-env.multicage-drive-comparison/v1',passed=all(r['passed'] for r in rows),full_declared_inventory_executed=tuple(names)==NAMES,rows=rows,producer=before['producer'],external_multicage_simulator=False,scope='authored 2-4 cage positive-Gc closed-loop drives; independent equations, not OEM validation or universal accuracy')
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


def verify_result(root):
    root=Path(root);manifest=verify(root);p=read_json(root/'protocol.json');names=p['cases']
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique cases required')
    if p!=protocol(names):raise ValueError('protocol/current producer differs')
    rows=[]
    for name in names:
        folder=root/name;study=document(name)
        if read_json(folder/'study.json')!=study:raise ValueError('registered study differs')
        refs=[read_json(folder/f'reference-{i}.json') for i in range(2)]
        for r,settings in zip(refs,REFERENCES):check_reference(r,settings)
        primaries=[]
        for i,fraction in enumerate(FRACTIONS):
            expected=copy.deepcopy(study);expected['adaptive']={'error_budget_fraction':fraction}
            if read_json(folder/f'primary-{i}-study.json')!=SwitchingDriveStudy.model_validate(expected).model_dump(mode='json'):raise ValueError('primary study differs')
            result=read_json(folder/f'primary-{i}.json')
            if result['code_sha256']!=p['producer']['package']:raise ValueError('primary producer differs')
            primaries.append(result)
        row=assess(name,study,refs,primaries)
        if row!=read_json(folder/'assessment.json'):raise ValueError('stored assessment differs')
        rows.append(row)
    summary=read_json(root/'summary.json')
    if summary['rows']!=rows or summary['producer']!=producer() or summary['passed']!=all(r['passed'] for r in rows) or summary['full_declared_inventory_executed']!=(tuple(names)==NAMES):raise ValueError('stored summary differs')
    if verify(root)!=manifest:raise ValueError('evidence changed during verification')
    return dict(verified=True,passed=summary['passed'],cases=len(rows),full_declared_inventory_executed=summary['full_declared_inventory_executed'],manifest_sha256=manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out');parser.add_argument('--python');parser.add_argument('--case',choices=NAMES,action='append');parser.add_argument('--verify')
    a=parser.parse_args()
    if a.verify:r=verify_result(a.verify);print(r)
    else:
        if not a.out or not a.python:parser.error('--out and --python required')
        r=run(a.out,a.python,tuple(a.case) if a.case else NAMES)
    raise SystemExit(0 if r['passed'] else 2)

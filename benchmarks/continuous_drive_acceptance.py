"""Full closed-loop continuous external-machine drive comparison.

Four authored profiles, original motulator 0.23.1, two continuous integrators,
independent bridge/source/FOC/thermal equations and fixed physical error caps.
Reference workers reject every production import. No performance ranking.
"""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import subprocess
from benchmarks.extended_closed_loop_acceptance import document as base
from benchmarks.continuous_drive_reference import VERSION,WHEEL_SHA256
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

NAMES=('linear_core_start','saturated_core_start','saturated_core_recovery','warm_core_start')
FRACTIONS=(.2,.05)
REFERENCES=(dict(method='DOP853',rtol=1e-12,atol=1e-14,max_step=2.5e-5),dict(method='Radau',rtol=3e-11,atol=3e-13,max_step=2.5e-5))
REFERENCE_UNCERTAINTY_FRACTION=.01


def document(name):
    if name not in NAMES:raise ValueError('unregistered continuous-drive case')
    p=base('saturated_single_core');p['id']='continuous-'+name
    if name=='linear_core_start':p['motor']['saturation_beta_per_vs']=0.
    if name=='saturated_core_recovery':
        p['grid_envelope']=[(0.,1.),(.012,1.),(.016,.5),(.024,.5),(.028,1.)];p['gate_block_windows']=[(.024,.028)]
    if name=='warm_core_start':
        w=base('warm_four_winding');p['motor']['thermal']=copy.deepcopy(w['motor']['thermal']);p['motor_initial_temperature_c']=[90.,70.];p['motor_temperature_trip_c']=[140.,140.]
        p['motor']['provenance']['thermal']='authored winding RC/R(T), same coefficients as warm-four benchmark; not identified OEM data'
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')


def producer():
    names=['continuous_drive_reference.py','continuous_drive_acceptance.py','extended_closed_loop_acceptance.py','switching_drive_cases.py','switching_extended_cases.py','switching_thermal_cases.py']
    return dict(package=package_fingerprint(),benchmark_sha256={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names})


def external(python,wheel,study,out,settings):
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',MPLCONFIGDIR='/tmp/pge-continuous-mpl')
    command=[str(python),'-m','benchmarks.continuous_drive_reference','--study',str(study),'--wheel',str(wheel),'--out',str(out),'--method',settings['method'],'--rtol',str(settings['rtol']),'--atol',str(settings['atol']),'--max-step',str(settings['max_step'])]
    completed=subprocess.run(command,capture_output=True,text=True,env=env,timeout=1200)
    out.with_suffix('.worker.log').write_text(completed.stdout+completed.stderr)
    if completed.returncode:raise RuntimeError('continuous worker failed: '+completed.stderr[-2000:])
    result=read_json(out)
    if not result['reference']['production_imports_blocked'] or result['reference']['machine_version']!=VERSION or not result.get('native_source_sha256'):raise ValueError('external reference identity/isolation differs')
    return result


def run(out,python,wheel,names=NAMES,reference_cache=None):
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique continuous cases required')
    if hashlib.sha256(Path(wheel).read_bytes()).hexdigest()!=WHEEL_SHA256:raise ValueError('continuous native wheel differs')
    before=producer();out=Path(out);out.mkdir(parents=True,exist_ok=False);limits=SwitchingTemporalLimits();protocol=dict(cases=list(names),primary_fractions=FRACTIONS,references=REFERENCES,reference_uncertainty_fraction=REFERENCE_UNCERTAINTY_FRACTION,limits=limits.model_dump(mode='json'),native_version=VERSION,native_wheel_sha256=WHEEL_SHA256,producer=before)
    cache=Path(reference_cache) if reference_cache else None
    cache_identity=verify(cache) if cache else None
    if cache:
        old=read_json(cache/'protocol.json')
        if old['producer']['benchmark_sha256']['continuous_drive_reference.py']!=before['benchmark_sha256']['continuous_drive_reference.py'] or old['references']!=list(REFERENCES) or old['native_wheel_sha256']!=WHEEL_SHA256:raise ValueError('independent reference cache producer/settings differ')
        protocol['reference_cache']=dict(manifest=cache_identity,scope='independent worker source, native wheel, solver controls and input study must match exactly; no primary cache reuse')
    (out/'protocol.json').write_bytes(encoded(protocol));rows=[]
    for name in names:
        folder=out/name;folder.mkdir();p=document(name);study=SwitchingDriveStudy.model_validate(p);(folder/'study.json').write_bytes(encoded(p));refs=[];reference_audits=[]
        try:
            for i,settings in enumerate(REFERENCES):
                if cache:
                    if read_json(cache/name/'study.json')!=p:raise ValueError('independent reference cache study differs')
                    source=cache/name/f'reference-{i}.json';ref=read_json(source)
                    if not ref['reference']['production_imports_blocked'] or ref['reference']['machine_version']!=VERSION:raise ValueError('untrusted reference cache isolation')
                    for key,value in settings.items():
                        field='max_step_s' if key=='max_step' else key
                        if ref['reference'][field]!=value:raise ValueError('reference cache integration controls differ')
                    (folder/f'reference-{i}.json').write_bytes(encoded(ref))
                else:ref=external(python,wheel,folder/'study.json',folder/f'reference-{i}.json',settings)
                refs.append(ref);reference_audits.append(audited(study,ref,limits))
            agreement=differences(study,refs[0],refs[1],limits)
            reference_passed=agreement['passed'] and all(g['maximum_normalized']<=REFERENCE_UNCERTAINTY_FRACTION for g in agreement['groups'].values()) and all(all(a.values()) for a in reference_audits) and all(r['metrics']['maximum_energy_relative_error']<1e-8 for r in refs)
            checks=[]
            for index,fraction in enumerate(FRACTIONS):
                candidate=copy.deepcopy(p);candidate['adaptive']={'error_budget_fraction':fraction};(folder/f'primary-{index}-study.json').write_bytes(encoded(SwitchingDriveStudy.model_validate(candidate).model_dump(mode='json')))
                result=run_switching_drive(candidate);(folder/f'primary-{index}.json').write_bytes(encoded(result));a=audited(study,result,limits);c=[differences(study,result,ref,limits) for ref in refs]
                check=dict(error_budget_fraction=fraction,passed=all(a.values()) and all(v['passed'] for v in c),audits=a,external_comparisons=c,integration=result['integration']);checks.append(check);print(name,fraction,check['passed'],flush=True)
            row=dict(case=name,passed=reference_passed and all(c['passed'] for c in checks),reference_agreement=agreement,reference_audits=reference_audits,reference_passed=reference_passed,primary_checks=checks)
        except (ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:row=dict(case=name,passed=False,error=str(exc));print(name,'failed',str(exc),flush=True)
        (folder/'assessment.json').write_bytes(encoded(row));rows.append(row)
    if producer()!=before:raise RuntimeError('continuous comparison producer changed during run')
    result=dict(schema='power-grid-env.full-continuous-drive-comparison/v1',passed=all(r['passed'] for r in rows),rows=rows,primary_trajectories=sum(len(r.get('primary_checks',[])) for r in rows),continuous_reference_trajectories=sum(2 for r in rows if 'reference_agreement' in r),external_machine=True,whole_external_simulator=False,absolute_error_certified=False,scope='four authored positive-Gc single-cage full closed-loop circuits; external unchanged motor RHS, independently assembled source/RL/diode/DC/body-diode/channel/control/thermal equations; DOP853 vs Radau agreement; no switching energy, active brake, device R(T), thermal trips or universal ranking')
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--python',required=True);p.add_argument('--wheel',required=True);p.add_argument('--case',action='append',choices=NAMES);p.add_argument('--reference-cache')
    a=p.parse_args();r=run(a.out,a.python,a.wheel,tuple(a.case) if a.case else NAMES,a.reference_cache);print(r['passed']);raise SystemExit(0 if r['passed'] else 2)

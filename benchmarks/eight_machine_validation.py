"""Frozen eight-machine native validation, no deletion of primary equipment.

Original NA-09 cases retain exact StudySpec identities. The extra motor-latch
case is separately named. Native references receive only the StudySpec.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity
from benchmarks.sparse_jacobian_validation import case as original
from benchmarks.eight_machine_circuit import PROFILE,extra_required

NAMES=('max_genrou_8_32','max_esst_8_32','max_genrou_long_8_32','motor_trip_latch')
TOLERANCES=(1e-12,1e-13)
SOURCE_CASE_HASHES={'max_genrou_8_32':'6dad8a5ca6df3c32da07d7ec2fc05eb25010cfa706b7fbe51413869165feea8d','max_esst_8_32':'d5e365fcce21f52a5da52f847b55eb557d52a6cc4bfd43381b7f60448f4fa29e','max_genrou_long_8_32':'349aabb32e2c0f7d46b0dad4a051ea72d2a507806debacf673ea0e333aa71f75'}


def case(name):
    if name not in NAMES:raise ValueError('registered eight-machine case required')
    s=original('max_genrou_8_32' if name=='motor_trip_latch' else name)
    if name=='motor_trip_latch':
        from power_grid_env.coupled_rms import CoupledEvent
        s.id='native-eight-motor-thermal-latch';s.coupled.motors[0].thermal.trip_level=.1
        s.coupled.events.append(CoupledEvent(time_s=.4,kind='motor_close',equipment_id=s.coupled.motors[0].id))
    return StudySpec.model_validate(s.model_dump(mode='json'))


def compare(spec,primary,native,*,expected_profile=PROFILE):
    from benchmarks.generator_oel2c_validation import compare as oel_compare
    from benchmarks.generator_esst1a_validation import compare as esst_compare
    if native.get('native_profile')!=expected_profile:raise ValueError('wrong native reference profile')
    audit=native.get('network_balance')
    if not audit or len(audit)!=len(native['time_s']):raise ValueError('missing independent balance audit')
    if not all(np.isfinite([x['kcl_pu'],x['power_balance_pu']]).all() for x in audit):raise ValueError('nonfinite independent balance audit')
    if any(x['kcl_pu']<0 or x['power_balance_pu']<0 for x in audit):raise ValueError('negative independent balance residual')
    maximum_kcl=max(x['kcl_pu'] for x in audit);maximum_power=max(x['power_balance_pu'] for x in audit)
    fn=esst_compare if spec.coupled.esst1a_exciters else oel_compare
    result=fn(spec,primary,native,extended=True)
    result.update(maximum_native_kcl_pu=maximum_kcl,maximum_native_power_balance_pu=maximum_power)
    result['passed']=bool(result['passed'] and maximum_kcl<=1e-8 and maximum_power<=1e-8)
    return result


def primary_worker(study,out):
    s=StudySpec.model_validate_json(Path(study).read_text());p=Path(out);p.mkdir(parents=True,exist_ok=False)
    a=run_study(s)
    (p/'primary.json').write_text(json.dumps(a,allow_nan=False)+'\n')
    expected_assembly='analytic_Schur_products' if s.coupled.integration_method=='network_dae' else 'column_blocks'
    if not a.get('completed') or a['integration']['jacobian_assembly']!=expected_assembly or a['integration'].get('jacobian_network_response')!=s.coupled.jacobian_network:raise ValueError('primary not completed with streamed CSC')


def native_worker(study,out,root,tolerance):
    s=StudySpec.model_validate_json(Path(study).read_text());code=package_fingerprint();source=source_identity()
    if s.coupled.esst1a_exciters:
        from benchmarks.esst1a_network import run
        b=run(s,out,root,tolerance,profile=PROFILE,audit_network=True,allow_cold_prestart=True,maximum_duration_s=6.)
    else:
        from benchmarks.oel2c_model_exchange import run
        b=run(s,out,root,tolerance,profile=PROFILE)
    if code!=package_fingerprint() or source!=source_identity() or b['study_sha256']!=canonical_hash(s):raise ValueError('native producer/input changed')
    b.update(worker_code_sha256=code,worker_source_sha256=source)
    Path(out,'reference.json').write_text(json.dumps(b,allow_nan=False)+'\n')


def comparison_worker(study,primary,reference,out):
    s=StudySpec.model_validate_json(Path(study).read_text());a=json.loads(Path(primary).read_text());b=json.loads(Path(reference).read_text())
    if a.get('code_sha256')!=package_fingerprint() or b.get('worker_code_sha256')!=package_fingerprint() or b.get('worker_source_sha256')!=source_identity() or b.get('study_sha256')!=canonical_hash(s):raise ValueError('comparison identities differ')
    check=compare(s,a,b);negative={}
    # Retain proofs that missing control/thermal/cage channels cannot pass.
    for label,needle in [('pss','.IEEEST.'),('thermal','.thermal_level'),('cage','.rotor1_current_pu')]:
        key=next(k for k in sorted(extra_required(s)) if needle in k)
        damaged=dict(b,channels=dict(b['channels']));damaged['channels'].pop(key,None)
        try:bad=compare(s,a,damaged)
        except ValueError as exc:negative[label]=str(exc)
        else:
            if bad['passed']:raise ValueError('missing required channel accepted: '+key)
            negative[label]='rejected'
    damaged=dict(b);damaged.pop('network_balance',None)
    try:compare(s,a,damaged)
    except ValueError as exc:negative['balance']=str(exc)
    else:raise ValueError('missing balance accepted')
    damaged=dict(b,network_balance=[dict(x) for x in b['network_balance']]);damaged['network_balance'][0]['kcl_pu']=-1.
    try:compare(s,a,damaged)
    except ValueError as exc:negative['negative_balance']=str(exc)
    else:raise ValueError('negative balance accepted')
    check['negative_checks']=negative
    if s.id=='native-eight-motor-thermal-latch':
        for report in [a,b]:
            if not any(e['kind']=='thermal_trip' and e['equipment_id']=='m0' for e in report['event_log']):raise ValueError('motor trip not exercised')
            if not any(e['kind']=='motor_close' and e['equipment_id']=='m0' and e.get('outcome')=='blocked_thermal_latch' for e in report['event_log']):raise ValueError('latched reclose not exercised')
        check['motor_trip_and_latched_reclose_exercised']=True
    Path(out).write_text(json.dumps(check,indent=2)+'\n')


def qualify(out,root,*,case_factory=case,expected_hashes=SOURCE_CASE_HASHES):
    from benchmarks.dynawo_reference_setup import verify as oel_verify
    from benchmarks.esst1a_native import verify as esst_verify
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();identity=dict(oel=oel_verify(root),esst=esst_verify(root));specs={}
    for n in NAMES:
        s=case_factory(n)
        if n in expected_hashes and canonical_hash(s)!=expected_hashes[n]:raise ValueError('NA09 input identity changed')
        p=out/n;p.mkdir();(p/'study.json').write_text(s.model_dump_json());specs[n]=dict(case_sha256=canonical_hash(s),study_path=n+'/study.json')
    del s
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=source,reference_identity=identity,cases=specs,native_tolerances=TOLERANCES,reference_profile=PROFILE,expected_case_hashes=expected_hashes,worker_timeout_s=900,native_work_budget_s=480,native_retained_scalar_budget=2_000_000,limits=dict(oel_state=1e-5,esst_state=2e-5,power_w_var=100.,thermal=1e-6,event_s=1e-6,balance_pu=1e-8,discrete=0.),native_before_primary=True,UEL_model='independently authored SI circle/lag; not native MNLEX2 FMU',reference='pinned Dynawo/OpenIPSL plus MSL PSS/PI in independent sparse SI MNA; not whole stock grid solver'),indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n');rows=[];env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    def execute(args,log):
        p=subprocess.run([sys.executable,'-m','benchmarks.eight_machine_validation',*args],env=env,capture_output=True,text=True,timeout=900);log.write_text(p.stdout+'\n'+p.stderr)
        if p.returncode:raise ValueError('worker failed: '+str(log))
    try:
        for n in NAMES:
            folder=out/n;study=folder/'study.json'
            for tol in TOLERANCES:
                target=folder/str(tol);execute(['--native','--study',str(study),'--out',str(target),'--root',str(Path(root).resolve()),'--tolerance',str(tol)],folder/(str(tol)+'.log'))
            execute(['--primary','--study',str(study),'--out',str(folder/'production')],folder/'production.log')
            for tol in TOLERANCES:
                target=folder/(str(tol)+'-comparison.json');execute(['--compare','--study',str(study),'--primary-file',str(folder/'production/primary.json'),'--reference-file',str(folder/str(tol)/'reference.json'),'--out',str(target)],folder/(str(tol)+'-comparison.log'))
                c=json.loads(target.read_text());rows.append(dict(case=n,tolerance=tol,**c));print(n,tol,c['passed'],flush=True)
                if not c['passed']:raise ValueError('eight-machine comparison failed: '+n)
        if code!=package_fingerprint() or source!=source_identity() or identity!=dict(oel=oel_verify(root),esst=esst_verify(root)):raise ValueError('producer/reference changed during qualification')
        result=dict(passed=True,status='complete',runs=rows,code_sha256=code,source_sha256=source,native_fresh_processes=8,primary_fresh_processes=4,comparison_fresh_processes=8,qualified_eight_machine_cases=NAMES,independent_host_verified=False,industrial_release_qualified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',runs=rows,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path);p.add_argument('--study',type=Path);p.add_argument('--primary',action='store_true');p.add_argument('--native',action='store_true');p.add_argument('--compare',action='store_true');p.add_argument('--primary-file',type=Path);p.add_argument('--reference-file',type=Path);p.add_argument('--tolerance',type=float,default=1e-12);a=p.parse_args()
    if a.primary:primary_worker(a.study,a.out)
    elif a.native:native_worker(a.study,a.out,a.root,a.tolerance)
    elif a.compare:comparison_worker(a.study,a.primary_file,a.reference_file,a.out)
    else:qualify(a.out,a.root)

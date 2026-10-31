"""Bounded ANDES group with online independent contactors and thermal replica."""
from pathlib import Path
import argparse,json,math,os,subprocess,sys,tempfile,time
import numpy as np
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import sha256_file,write_json
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.ipc import kill_process_group
from benchmarks.andes_group_motor_reference import scope,worker
from benchmarks.native_closed_driver import NativeLoop
from benchmarks.native_terminal_measurements import reconstruct_terminals

SOURCE_ROOT=Path(__file__).resolve().parents[1]
DRIVER_FILES=('benchmarks/native_closed_driver.py','benchmarks/native_closed_loop.py','benchmarks/native_checkpoint.py','benchmarks/native_terminal_measurements.py','benchmarks/andes_group_motor_reference.py','benchmarks/andes_motor_terminal.py','benchmarks/double_cage_andes_limits.py')

def source_snapshot():return {p:sha256_file(SOURCE_ROOT/p) for p in DRIVER_FILES}


def plant_case(spec):
    data=spec.model_dump(mode='json');key='coupled' if spec.coupled else 'induction'
    for m in data[key]['motors']:m['contactor']=None;m['thermal']=None
    result=StudySpec.model_validate(data);scope(result)
    s=spec.coupled if spec.coupled else spec.induction
    if not any(m.contactor or m.thermal for m in s.motors):raise ValueError('online native controller configuration required')
    channel_count=9*len(s.motors)+6*sum(bool(m.contactor) for m in s.motors)+2*sum(bool(m.thermal) for m in s.motors)+10*len(spec.network.generators)
    rows=4*s.duration_s/s.step_s+2*(len(s.events)+s.maximum_contactor_transitions+len(s.motors))+4
    if rows*channel_count*32>30_000_000:raise ValueError('online native output budget')
    return result


def execute(spec,step):
    sources=source_snapshot();code_before=package_fingerprint()
    plant=plant_case(spec);result=worker(plant,step,driver=NativeLoop(spec));meta=result['native_driver_metadata']
    if sources!=source_snapshot() or code_before!=package_fingerprint():raise ValueError('source changed during online native integration')
    extra=meta['auxiliary_channels']
    if set(extra)&set(result['channels']):raise ValueError('native control channel collision')
    result['channels'].update(extra);meta['auxiliary_channels']={}
    result['original_study_sha256']=canonical_hash(spec);result['plant_study_sha256']=canonical_hash(plant)
    result['online_driver_source_sha256']=sources
    event_times={e['time_s'] for e in meta['event_log']}
    observer=reconstruct_terminals(plant,result,additional_event_times=event_times)
    errors={}
    settings=spec.coupled if spec.coupled else spec.induction
    for m in settings.motors:
        if m.contactor:
            expected=np.array(observer['node_voltage_pu'][m.contactor.sense_node_id]);actual=np.array(result['channels'][m.id+'.contactor_sensed_voltage_pu'])
            errors[m.id]=float(max(abs(expected-actual)))
    if any(not np.isfinite(e) or e>1e-6 for e in errors.values()):raise ValueError('online native sensing disagrees with port observer')
    result['online_sensor_observer_errors_pu']=errors;result['controller_commands_precomputed']=False
    result['custom_online_native_driver']=True;result['stock_native_controller_qualified']=False
    return result


def native_reference(spec,step,timeout_s=120.):
    plant_case(spec);expected_sources=source_snapshot();expected_code=package_fingerprint()
    if not math.isfinite(step) or not 1e-5<=step<=.01 or not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('bounded online native step and positive timeout required')
    with tempfile.TemporaryDirectory(prefix='pge-native-online-') as tmp:
        root=Path(tmp);write_json(root/'study.json',spec.model_dump(mode='json'));env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        with (root/'log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','benchmarks.native_closed_loop','--native',str(root/'study.json'),'--step',str(step),'--out',str(root/'result.json')],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);start=time.monotonic();reason=None
            try:
                while process.poll() is None:
                    if time.monotonic()-start>=timeout_s:reason='native_timeout'
                    elif (root/'log').stat().st_size>1_000_000 or ((root/'result.json').exists() and (root/'result.json').stat().st_size>32_000_000):reason='native_output_budget'
                    if reason:kill_process_group(process);break
                    time.sleep(.01)
                if reason:return dict(completed=False,status=reason)
                if (root/'log').stat().st_size>1_000_000:return dict(completed=False,status='native_output_budget')
                if process.returncode!=0 or not (root/'result.json').exists():return dict(completed=False,status='native_worker_failed',exit_code=process.returncode,log_tail=(root/'log').read_text(errors='replace')[-2500:])
                raw=(root/'result.json').read_bytes()
                if len(raw)>32_000_000:return dict(completed=False,status='native_output_budget')
                r=json.loads(raw)
                if r.get('original_study_sha256')!=canonical_hash(spec) or r.get('code_sha256')!=package_fingerprint() or r.get('step_s')!=step or r.get('backend_version')!='2.0.0':raise ValueError('online native identity mismatch')
                if r['online_driver_source_sha256']!=expected_sources or expected_sources!=source_snapshot() or expected_code!=package_fingerprint():raise ValueError('online native producer revision mismatch')
                for p,h in r['online_driver_source_sha256'].items():
                    if sha256_file(SOURCE_ROOT/p)!=h:raise ValueError('online native source changed: '+p)
                return r
            finally:
                if process.poll() is None:kill_process_group(process)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--native',required=True,type=Path);p.add_argument('--step',required=True,type=float);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    write_json(a.out,execute(StudySpec.model_validate_json(a.native.read_text()),a.step))

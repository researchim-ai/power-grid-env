"""Explicit RTE1888 admission followed by independently checked CPF/envelope.

The original invalid schedules remain a rejection case. The derived profile is
identified separately and never relabelled as the unchanged published input.
"""
import argparse,hashlib,json,os,subprocess,sys,shutil,tempfile,zipfile,stat
from pathlib import Path
from dataclasses import replace
import numpy as np
from scipy.optimize import brentq
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,extract_zip
from power_grid_env.balanced_ac import case_from_document,case_document
from power_grid_env.ac_schedule import run_schedule,verify_schedule,prepare_schedule
from power_grid_env.evidence import package_fingerprint
from benchmarks.ac_scale_acceptance import compare_states,physical,validate_measurement,requested_step_comparison
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'cases/ac-scale/rte1888.json'
STEPS=(.1,.05)


def schedule_input():
    source=read_json(SOURCE)
    return dict(case=source['start'],options=dict(participants=[dict(generator_index=i,weight=1.) for i in range(len(source['start']['generators']))]),provenance=dict(source=str(SOURCE.relative_to(ROOT)),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),policy='explicit derived schedule, original bounds and total generation preserved; not published operating point'))


def study_input(prepared,step):
    source=read_json(SOURCE);original=case_from_document(source['start']);target=case_from_document(source['target']);admitted=case_from_document(prepared['prepared_case'])
    derived=replace(target,generators=[replace(g,pg=p.pg) for g,p in zip(target.generators,admitted.generators,strict=True)])
    if any(replace(p,pg=g.pg)!=g for p,g in zip(admitted.generators,original.generators,strict=True)):raise ValueError('admission changed equipment')
    source['start']=case_document(admitted);source['target']=case_document(derived);source['options'].update(step=step,maximum_step=step)
    source['provenance']=dict(source_schedule_sha256=prepared['input_case_sha256'],profile='RTE1888 explicit equal-weight Pg projection, total preserved, unchanged P/Q/network limits; +1% Pd/Qd')
    return source


def independent_projection(d,r):
    """Separate scalar brentq reference, not native projection implementation."""
    case=case_from_document(d['case']);original=np.array([g.pg for g in case.generators]);lo=np.array([g.pmin for g in case.generators]);hi=np.array([g.pmax for g in case.generators]);total=float(sum(original))
    shift=brentq(lambda x:float(sum(np.clip(original+x,lo,hi)))-total,float(min(lo-original)),float(max(hi-original)),xtol=1e-12)
    reference=np.clip(original+shift,lo,hi);actual=np.array([g['pg'] for g in r['prepared_case']['generators']]);error=float(max(abs(actual-reference)))
    return dict(method='independent SciPy brentq, equal weights',maximum_pg_error_mw=error,total_error_mw=abs(float(sum(actual))-total),passed=error<=1e-7 and abs(float(sum(actual))-total)<=1e-7)


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/ac_scale_acceptance.py',ROOT/'benchmarks/ac_dispatch_sparse_reference.py',ROOT/'benchmarks/ac_dispatch_acceptance.py',ROOT/'benchmarks/ac_dispatch_arc_acceptance.py',SOURCE]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _measured_row(step,d,folder,stored=False,runs=None):
    from power_grid_env.ac_envelope import verify_envelope
    runs=folder/'runs' if runs is None else runs
    verify(runs);m=read_json(runs/'measurement.json');validate_measurement(m)
    if m['producer_sha256']!=package_fingerprint() or m['input_sha256']!=hashlib.sha256(encoded(d)).hexdigest():raise ValueError('measurement identity differs')
    if len(m['physical_hashes'])!=3:raise ValueError('three completed admission repeats required')
    hashes=[]
    for i in range(3):
        run=runs/str(i);verify_envelope(run)
        if encoded(read_json(run/'input.json'))!=encoded(d):raise ValueError('repeat input differs')
        study=dict(trace=read_json(run/'trace.json'),envelope=read_json(run/'envelope.json'));hashes.append(hashlib.sha256(encoded(physical(study))).hexdigest())
    expected_result=dict(completed=bool(study['trace']['completed'] and study['envelope']['completed']),status=study['trace']['reason'],monitor_status=study['envelope']['reason'],accepted_points=len(study['trace']['points']),operating_events=len(study['envelope']['events']),p_events=len(study['trace']['p_control_changes']),q_events=len(study['trace']['control_changes']),timeline_complete=study['envelope']['timeline_complete'],verified=True)
    if hashes!=m['physical_hashes'] or m['result']!=expected_result:raise ValueError('repeat physical receipt differs')
    references,comparison=compare_states(d,runs/'0',read_references(folder) if stored else None)
    if not stored:
        target=folder/'references';target.mkdir()
        for i,start in enumerate(range(0,len(references),16)):(target/f'{i:04}.json').write_bytes(encoded(references[start:start+16]))
    completed=bool(study['trace']['completed'] and study['envelope']['completed']);repeat=len(set(hashes))==1
    return dict(case='rte1888-admitted',step=step,completed=completed,repeat_physical_equal=repeat,median_wall_s=m['median_wall_s'],median_cpu_s=m['median_cpu_s'],process_peak_rss_mib=m['process_peak_rss_mib'],comparison=comparison,events=comparison['events'],passed=bool(completed and repeat and comparison['passed'] and all(e['localized'] for e in comparison['events'])))


def read_references(folder):
    files=sorted((folder/'references').iterdir());result=[]
    if [p.name for p in files]!=[f'{i:04}.json' for i in range(len(files))]:raise ValueError('reference shard inventory differs')
    for path in files:
        values=read_json(path)
        if not isinstance(values,list) or not 1<=len(values)<=16:raise ValueError('invalid reference shard')
        result.extend(values)
    return result


def measured_row(step,d,folder,stored=False):
    if (folder/'runs').exists():return _measured_row(step,d,folder,stored)
    with tempfile.TemporaryDirectory(prefix='pge-admission-check-') as temporary:
        runs=extract_zip(folder/'runs.zip',Path(temporary)/'runs')
        return _measured_row(step,d,folder,stored,runs)


def compact_runs(folder):
    """Compress verified data without raising global artifact byte budgets."""
    runs=folder/'runs';identity=verify(runs);archive=folder/'runs.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for p in sorted(runs.rglob('*')):
            if p.is_file():
                info=zipfile.ZipInfo(p.relative_to(runs).as_posix(),date_time=(1980,1,1,0,0,0));info.external_attr=(stat.S_IFREG|0o644)<<16;info.compress_type=zipfile.ZIP_DEFLATED
                output.writestr(info,p.read_bytes())
    with tempfile.TemporaryDirectory(prefix='pge-admission-zip-') as temporary:
        restored=extract_zip(archive,Path(temporary)/'restored')
        if verify(restored)!=identity:raise ValueError('compressed native evidence differs')
    if verify(runs)!=identity:raise ValueError('native evidence changed during compression')
    shutil.rmtree(runs)


def qualify(out,reuse=None):
    from power_grid_env.ac_dispatch import solve_dispatch
    from power_grid_env.ipc import kill_process_group
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();d=schedule_input()
    try:solve_dispatch(case_from_document(d['case']),read_json(SOURCE)['options']['dispatch'])
    except ValueError as exc:rejection=str(exc)
    else:raise ValueError('original invalid schedule unexpectedly admitted')
    prepared=run_schedule(d,out/'schedule');verify_schedule(out/'schedule');projection=independent_projection(d,prepared);rows=[]
    for step in STEPS:
        input_doc=study_input(prepared,step);folder=out/str(step);folder.mkdir();(folder/'input.json').write_bytes(encoded(input_doc))
        env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','MPLCONFIGDIR':'/tmp/pge-scale-mpl'}
        if reuse is not None:
            previous=Path(reuse)/str(step)/'runs';verify(previous)
            shutil.copytree(previous,folder/'runs')
        else:
            with (folder/'worker.log').open('wb') as log:
                p=subprocess.Popen([sys.executable,'-m','benchmarks.ac_scale_acceptance','--worker','--input',str((folder/'input.json').resolve()),'--out',str((folder/'runs').resolve())],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:p.wait(timeout=600)
                except subprocess.TimeoutExpired:kill_process_group(p)
            if p.returncode!=0:raise ValueError('admitted RTE worker failed')
        row=measured_row(step,input_doc,folder);compact_runs(folder);rows.append(row);print(json.dumps(row),flush=True)
    step_check=requested_step_comparison('rte1888-admitted',rows)
    summary=dict(execution_policy='verified reused sealed worker measurements; no new timing run' if reuse is not None else 'fresh native workers',schema='pge-ac-schedule-qualification/v1',producer_sha256=producer,source_hashes=hashes,projection=projection,rows=rows,step_comparison=step_check,original_schedule_rejected=rejection,original_profile_qualified=False,derived_profile_qualified=True,superiority_proven=False,passed=bool(projection['passed'] and all(r['passed'] for r in rows) and step_check['passed'] and producer==package_fingerprint() and hashes==sources()))
    summary['derived_profile_qualified']=summary['passed'];(out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json')
    if s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current producers required')
    if s['execution_policy'] not in ['fresh native workers','verified reused sealed worker measurements; no new timing run']:raise ValueError('execution policy differs')
    if s['schema']!='pge-ac-schedule-qualification/v1':raise ValueError('admission schema differs')
    from power_grid_env.ac_dispatch import DispatchSystem,DispatchOptions
    try:DispatchSystem(case_from_document(schedule_input()['case']),DispatchOptions.model_validate(read_json(SOURCE)['options']['dispatch']))
    except ValueError as exc:rejection=str(exc)
    else:raise ValueError('original invalid schedule unexpectedly admitted')
    if s['original_schedule_rejected']!=rejection:raise ValueError('original rejection differs')
    verify_schedule(out/'schedule');d=read_json(out/'schedule/input.json');prepared=read_json(out/'schedule/result.json')
    if encoded(d)!=encoded(schedule_input()):raise ValueError('registered original input differs')
    projection=independent_projection(d,prepared);rows=[]
    for step in STEPS:
        expected=study_input(prepared,step);folder=out/str(step)
        if encoded(read_json(folder/'input.json'))!=encoded(expected):raise ValueError('derived input differs')
        rows.append(measured_row(step,expected,folder,True))
    check=requested_step_comparison('rte1888-admitted',rows);passed=bool(projection['passed'] and all(r['passed'] for r in rows) and check['passed'])
    if s['rows']!=rows or s['projection']!=projection or s['step_comparison']!=check or s['passed']!=passed or s['derived_profile_qualified']!=passed or s['original_profile_qualified'] or s['superiority_proven']:raise ValueError('admission qualification conclusion differs')
    return dict(verified=True,passed=passed,manifest_sha256=identity,scope='explicit derived Pg, repeated native physical studies and stored independent reference comparisons; not original RTE qualification or authenticated timing')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');p.add_argument('--reuse');a=p.parse_args();r=qualify(a.out,a.reuse) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

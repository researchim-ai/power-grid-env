"""Strict read-only restart evidence verification and separately persisted replay.

Hashes detect corruption; coherent fabricated data are not authenticated by a
manifest. Static verification recomputes decisions, replay reruns the model.
"""
from __future__ import annotations
import hashlib
import math
from pathlib import Path
import numpy as np
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.motor_bundle import _json,_validate_result,_channel_group,_event_matches,runtime_identity
from power_grid_env.motor_restart import MotorRestartProtocol,build_restart_points,_voltage_assessment,_selection,run_motor_restart
from power_grid_env.motor_sensitivity import _assess,_encoded,MAX_ARTIFACT_BYTES
from power_grid_env.study_spec import StudySpec

MAX_TOTAL_BYTES=512_000_000
BASE={'base-study.json','protocol.json','environment.json','run.json'}
CASE_NAMES={f'case-{i:03d}' for i in range(32)}


def _read(path,limit):
    with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('audit member byte budget exceeded: '+path.name)
    return raw


def _digest_file(path):
    digest=hashlib.sha256();size=0
    with path.open('rb') as stream:
        while block:=stream.read(1_000_000):
            size+=len(block)
            if size>MAX_ARTIFACT_BYTES:raise ValueError('audit member byte budget exceeded')
            digest.update(block)
    return digest.hexdigest()


def _verified(directory):
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():raise ValueError('ordinary experiment directory required')
    members=list(root.iterdir())
    if len(members)>37:raise ValueError('unexpected experiment inventory')
    files={};dirs=set()
    for p in members:
        if p.is_symlink():raise ValueError('experiment links forbidden')
        if p.is_file() and p.name in BASE|{'manifest.json'}:files[p.name]=p
        elif p.is_dir() and p.name in CASE_NAMES:
            dirs.add(p.name);children=list(p.iterdir())
            if {c.name for c in children}!={'study.json','result.json'} or any(c.is_symlink() or not c.is_file() for c in children):raise ValueError('incomplete or unsafe case inventory')
            files.update({p.name+'/'+c.name:c for c in children})
        else:raise ValueError('unexpected experiment member: '+p.name)
    if not (BASE|{'manifest.json'})<=files.keys():raise ValueError('missing experiment metadata')
    total=sum(p.stat().st_size for p in files.values())
    if total>MAX_TOTAL_BYTES:raise ValueError('experiment total byte budget exceeded')
    manifest_raw=_read(files.pop('manifest.json'),32_000);manifest=_json(manifest_raw)
    if not isinstance(manifest,dict) or set(manifest)!=set(files):raise ValueError('manifest inventory mismatch')
    if any(not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in manifest.values()):raise ValueError('invalid manifest checksum')
    for name,path in files.items():
        if _digest_file(path)!=manifest[name]:raise ValueError('experiment checksum mismatch: '+name)
    def load(name,limit=MAX_ARTIFACT_BYTES):
        raw=_read(files[name],limit)
        if hashlib.sha256(raw).hexdigest()!=manifest[name]:raise ValueError('experiment changed while reading: '+name)
        return _json(raw)
    protocol=MotorRestartProtocol.model_validate(load('protocol.json',100_000));spec=StudySpec.model_validate(load('base-study.json',32_000_000));run=load('run.json',2_000_000)
    if total>protocol.maximum_output_bytes:raise ValueError('declared output budget exceeded')
    if not isinstance(run,dict) or run.get('completed') is not True or run.get('status')!='completed':raise ValueError('completed experiment required for verification')
    if run.get('code_sha256')!=package_fingerprint():raise ValueError('experiment code mismatch; use matching archived wheel')
    environment=load('environment.json',256_000)
    if environment!=runtime_identity():raise ValueError('experiment runtime mismatch; use recorded dependency lock')
    points=build_restart_points(spec,protocol)
    if dirs!={p['id'] for p in points}:raise ValueError('missing or extra candidate directory')
    elapsed=run.get('elapsed_s')
    if type(elapsed) not in (float,int) or not math.isfinite(elapsed) or elapsed<0:raise ValueError('invalid elapsed time')
    rows=[]
    for point in points:
        name=point['id'];variant=StudySpec.model_validate(load(name+'/study.json',32_000_000))
        if canonical_hash(variant)!=canonical_hash(point['study']):raise ValueError('candidate differs from frozen protocol')
        report=load(name+'/result.json');_validate_result(variant,report)
        observations={o.motor_id:_assess(variant,report,o) for o in protocol.observations};extra=_voltage_assessment(variant,report,protocol)
        row={k:v for k,v in point.items() if k not in {'study','error'}}
        row.update(numerically_completed=True,within_limits=all(v['within_limits'] for v in observations.values()) and extra['additional_within_limits'],observations=observations,delta_from_baseline={},study_sha256=canonical_hash(variant),status='completed',**extra)
        baseline=observations if not rows else rows[0]['observations']
        for motor,observation in observations.items():
            metrics=baseline[motor]['metrics'];row['delta_from_baseline'][motor]={k:float(v-metrics[k]) for k,v in observation['metrics'].items() if type(v) in (float,int) and type(metrics.get(k)) in (float,int)}
        rows.append(row)
    envelope={}
    for o in protocol.observations:
        values=[r['observations'][o.motor_id]['metrics'] for r in rows]
        envelope[o.motor_id]={k:dict(minimum=min(v[k] for v in values if type(v.get(k)) in (float,int)),maximum=max(v[k] for v in values if type(v.get(k)) in (float,int)),available_cases=sum(type(v.get(k)) in (float,int) for v in values)) for k in values[0] if any(type(v.get(k)) in (float,int) for v in values)}
    expected=dict(schema_version='power-grid-env.motor-restart-result/v1',completed=True,status='completed',all_cases_within_limits=all(r['within_limits'] for r in rows),engineering_assessment='',expected_cases=len(points),attempted_cases=len(rows),cases=rows,code_sha256=package_fingerprint(),base_study_sha256=canonical_hash(spec),protocol_sha256=canonical_hash(protocol),protocol=protocol.model_dump(mode='json'),elapsed_s=elapsed,environment_sha256=manifest['environment.json'],scope_note='Finite declared samples only; no probability, interval-wide or global robustness guarantee',industrial_profile_qualified=False,field_validated=False,sampled_envelope=envelope)
    expected.update(_selection(rows,True))
    # Dict equality would accept True as 1; encoded JSON preserves their types.
    from power_grid_env.motor_bundle import _encoded as canonical_encoded
    if canonical_encoded(run)!=canonical_encoded(expected):raise ValueError('stored assessment differs from recomputed results')
    if hashlib.sha256(_read(root/'manifest.json',32_000)).digest()!=hashlib.sha256(manifest_raw).digest() or any(_digest_file(p)!=manifest[n] for n,p in files.items()):raise ValueError('experiment changed during verification')
    if runtime_identity()!=environment or package_fingerprint()!=run['code_sha256']:raise ValueError('code/runtime changed during verification')
    return spec,protocol,points,run,hashlib.sha256(manifest_raw).hexdigest()


def verify_motor_restart(directory):
    _,_,points,run,signature=_verified(directory)
    return dict(verified=True,status='verified_completed_experiment',cases_verified=len(points),candidates_within_sampled_limits=run['candidates_within_sampled_limits'],code_sha256=package_fingerprint(),manifest_sha256=signature,trajectory_resimulated=False,provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)


def _compare_reports(expected,actual,criteria):
    if expected['sample_side']!=actual['sample_side'] or len(expected['time_s'])!=len(actual['time_s']) or set(expected['channels'])!=set(actual['channels']):return dict(passed=False,reason='time_side_or_channel_inventory_mismatch')
    time_error=float(max(abs(np.asarray(expected['time_s'])-actual['time_s'])));groups={};failed=[]
    for key,values in expected['channels'].items():
        group=_channel_group(key);error=np.asarray(actual['channels'][key])-values
        if group=='angle_absolute_rad':error=np.angle(np.exp(1j*error))
        maximum=float(max(abs(error)));limit=0. if group in {'discrete','count'} else getattr(criteria,group)
        row=groups.setdefault(group,dict(channels=0,maximum_absolute_error=0.,absolute_tolerance=limit));row['channels']+=1;row['maximum_absolute_error']=max(maximum,row['maximum_absolute_error'])
        if not np.isfinite(maximum) or maximum>limit:
            if len(failed)<16:failed.append(key)
    events_match=_event_matches(actual['event_log'],expected['event_log'],criteria)
    return dict(passed=time_error<=criteria.time_absolute_s and events_match and not failed,maximum_time_error_s=time_error,event_audit_matches=events_match,channel_groups=groups,first_failed_channels=failed)


def replay_motor_restart(directory,out,*,cancelled=None):
    spec,protocol,points,expected,signature=_verified(directory);root=Path(directory).resolve()
    if Path(out).is_symlink():raise ValueError('ordinary replay output directory required')
    out=Path(out).resolve()
    if out==root or out.is_relative_to(root) or root.is_relative_to(out):raise ValueError('replay output must be disjoint from original experiment')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ValueError('new or empty replay directory required')
    out.mkdir(parents=True,exist_ok=True)
    actual=run_motor_restart(spec,protocol,out/'experiment',cancelled=cancelled);rows=[];status=actual['status'];decisions=False
    if actual['completed']:
        try:
            _,_,_,_,after=_verified(root)
            if after!=signature:raise ValueError('original manifest changed during replay')
            _,_,_,_,new_signature=_verified(out/'experiment')
            old_inventory=_read(root/'manifest.json',32_000);new_inventory=_read(out/'experiment'/'manifest.json',32_000)
            if hashlib.sha256(old_inventory).hexdigest()!=signature or hashlib.sha256(new_inventory).hexdigest()!=new_signature:raise ValueError('manifest changed before comparison')
            old_manifest=_json(old_inventory);new_manifest=_json(new_inventory)
            for point in points:
                name=point['id'];old_raw=_read(root/name/'result.json',MAX_ARTIFACT_BYTES);new_raw=_read(out/'experiment'/name/'result.json',MAX_ARTIFACT_BYTES)
                if hashlib.sha256(old_raw).hexdigest()!=old_manifest[name+'/result.json'] or hashlib.sha256(new_raw).hexdigest()!=new_manifest[name+'/result.json']:raise ValueError('result changed during comparison')
                row=_compare_reports(_json(old_raw),_json(new_raw),protocol.repeatability);row['candidate_id']=point['candidate_id'];rows.append(row)
            if _digest_file(root/'manifest.json')!=signature or _digest_file(out/'experiment'/'manifest.json')!=new_signature or package_fingerprint()!=expected['code_sha256']:raise ValueError('original/code changed during replay')
            decisions=actual['candidates_within_sampled_limits']==expected['candidates_within_sampled_limits'] and [r['within_limits'] for r in actual['cases']]==[r['within_limits'] for r in expected['cases']]
            passed=all(r['passed'] for r in rows) and decisions;status='repeatable' if passed else 'outside_repeatability_limits'
        except (ValueError,OSError,TypeError,KeyError) as exc:
            passed=False;status='verification_failed_after_replay';rows.append(dict(passed=False,error=str(exc)[:400]))
    else:passed=False;decisions=False
    result=dict(completed=bool(actual['completed']) and status not in {'verification_failed_after_replay'},repeatable=passed,status=status,candidate_decisions_match=decisions,cases=rows,code_sha256=package_fingerprint(),original_manifest_sha256=signature,repeatability=protocol.repeatability.model_dump(mode='json'),scope_note='Fresh model replay of every declared candidate; checksums are not field provenance',provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)
    payload=_encoded(result)
    if len(payload)>128_000:raise ValueError('replay summary byte budget')
    (out/'replay.json').write_bytes(payload)
    files={}
    for p in sorted(out.rglob('*')):
        if p.is_symlink():raise ValueError('replay artifact links forbidden')
        if p.is_file():files[str(p.relative_to(out))]=_digest_file(p)
    (out/'replay-manifest.json').write_bytes(_encoded(files))
    return result

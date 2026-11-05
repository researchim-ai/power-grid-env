"""Bounded, immutable engineering DOL/restart study packs.

Every motor and voltage window is explicitly assessed. Sampled quantities are
labelled as such; regression expectations never become an engineering approval.
Studies execute in the existing isolated trusted worker, with deadlines and
bounded output. This is not a site/OEM acceptance certificate or OS sandbox.
"""
from pathlib import Path
import hashlib,json,math,time
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.study_spec import StudySpec
from power_grid_env.motor_sensitivity import RecoveryWindow
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.topology import compile_topology


class MotorDOLCriteria(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    motor_id: str=Field(min_length=1,max_length=128)
    minimum_final_speed_pu: float=Field(ge=0)
    maximum_sampled_current_pu: float=Field(gt=0)
    expected_connected_at_end: bool=True
    maximum_thermal_replica_level: float | None=Field(default=None,ge=0)
    expected_thermal_latch: bool | None=None
    recovery: RecoveryWindow | None=None


class DOLVoltageWindow(BaseModel):
    start_side: Literal['right','both']='right'
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    node_id: str=Field(min_length=1,max_length=128)
    from_s: float=Field(ge=0)
    to_s: float=Field(gt=0)
    minimum_voltage_pu: float=Field(ge=0)
    maximum_voltage_pu: float=Field(gt=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.to_s<=self.from_s or self.maximum_voltage_pu<self.minimum_voltage_pu:raise ValueError('invalid DOL voltage window')
        return self


class IndustrialDOLCase(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(pattern='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    study: StudySpec
    parameter_provenance: str=Field(min_length=1,max_length=4096)
    motors: list[MotorDOLCriteria]=Field(default_factory=list,max_length=128)
    voltage_windows: list[DOLVoltageWindow]=Field(default_factory=list,max_length=64)
    expectation: Literal['within_limits','violates_limits','rejected']='within_limits'
    rejection_reason: str | None=Field(default=None,max_length=128)

    @model_validator(mode='after')
    def coverage(self):
        s=self.study
        if s.study not in {'motor_start','coupled_rms'} or s.backend!='scipy':raise ValueError('DOL pack supports motor_start/coupled_rms with scipy')
        settings=s.coupled if s.study=='coupled_rms' else s.induction
        if settings is None:raise ValueError('explicit DOL settings required')
        if not self.parameter_provenance.strip():raise ValueError('parameter provenance must not be blank')
        if self.expectation=='rejected':
            if not self.rejection_reason:raise ValueError('expected rejection needs an exact reason')
        else:
            if self.rejection_reason is not None:raise ValueError('rejection reason only applies to rejected cases')
            ids=[o.motor_id for o in self.motors];by_id={m.id:m for m in settings.motors}
            if len(set(ids))!=len(ids) or set(ids)!=set(by_id):raise ValueError('DOL criteria must cover every motor exactly once')
            if not self.voltage_windows:raise ValueError('explicit voltage acceptance windows required')
            nodes={n.id for n in s.network.connectivity_nodes}
            for window in self.voltage_windows:
                if window.node_id not in nodes or window.to_s>settings.duration_s:raise ValueError('voltage window outside declared network/horizon')
            for o in self.motors:
                m=by_id[o.motor_id]
                if m.thermal is None and (o.maximum_thermal_replica_level is not None or o.expected_thermal_latch is not None):raise ValueError('thermal criteria require a thermal passport')
                if o.recovery and (o.recovery.from_s+o.recovery.maximum_delay_s>settings.duration_s or settings.step_s>o.recovery.dwell_s/2):raise ValueError('sampled recovery window requires full horizon and at least two output steps per dwell')
        return self


class IndustrialDOLPack(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal['power-grid-env.industrial-dol/v1']='power-grid-env.industrial-dol/v1'
    id: str=Field(pattern='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    purpose: Literal['engineering','regression']='engineering'
    cases: list[IndustrialDOLCase]=Field(min_length=1,max_length=32)
    per_case_timeout_s: float=Field(default=600.,gt=0,le=3600.)
    total_timeout_s: float=Field(default=1800.,gt=0,le=43200.)
    maximum_output_bytes: int=Field(default=256000000,ge=1000000,le=1024000000)

    @model_validator(mode='after')
    def explicit_scope(self):
        if len({c.id for c in self.cases})!=len(self.cases):raise ValueError('unique study case IDs required')
        if self.purpose=='engineering' and any(c.expectation!='within_limits' for c in self.cases):raise ValueError('expected violations/rejections require explicit regression purpose')
        return self


def encode(value):return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf8')


def assessment(case,result):
    settings=case.study.coupled if case.study.study=='coupled_rms' else case.study.induction
    if not result.get('completed'):raise ValueError('completed trace required for engineering assessment')
    t=np.asarray(result.get('time_s',[]),float);sides=result.get('sample_side',[]);channels=result.get('channels',{})
    if t.ndim!=1 or len(t)<2 or not np.isfinite(t).all() or len(sides)!=len(t) or np.any(np.diff(t)<0) or abs(t[0])>1e-12 or abs(t[-1]-settings.duration_s)>1e-9 or max(np.diff(t))>settings.step_s+1e-9:raise ValueError('incomplete or invalid DOL time grid')
    for start in np.arange(math.ceil(settings.duration_s/settings.step_s))*settings.step_s:
        if np.min(abs(t-start))>1e-9:raise ValueError('missing authored DOL output sample')
    def channel(key,discrete=False):
        v=np.asarray(channels.get(key,[]),float)
        if v.shape!=t.shape or not np.isfinite(v).all() or (discrete and np.any((v!=0)&(v!=1))):raise ValueError('missing/invalid DOL channel: '+key)
        return v
    keep=np.r_[np.diff(t)!=0,True];rows={};all_checks=[]
    for o in case.motors:
        speed=channel(o.motor_id+'.speed_pu');current=channel(o.motor_id+'.current_pu');
        if np.any(current<0):raise ValueError('negative current magnitude')
        connected=channel(o.motor_id+'.connected',True)
        metrics=dict(final_speed_pu=float(speed[-1]),peak_sampled_current_pu=float(max(current)),connected_at_end=bool(connected[-1]))
        checks=dict(final_speed=metrics['final_speed_pu']>=o.minimum_final_speed_pu,current=metrics['peak_sampled_current_pu']<=o.maximum_sampled_current_pu,connected=metrics['connected_at_end']==o.expected_connected_at_end)
        if o.maximum_thermal_replica_level is not None:
            heat=channel(o.motor_id+'.thermal_level');metrics['maximum_sampled_thermal_replica_level']=float(max(heat));checks['thermal_level']=max(heat)<=o.maximum_thermal_replica_level
        if o.expected_thermal_latch is not None:
            latch=channel(o.motor_id+'.thermal_latched',True);metrics['thermal_latched_at_end']=bool(latch[-1]);checks['thermal_latch']=bool(latch[-1])==o.expected_thermal_latch
        if o.recovery:
            w=o.recovery;begin=confirmed=None
            for moment,value,on in zip(t[keep],speed[keep],connected[keep]):
                if moment<w.from_s:continue
                if not on or value<w.minimum_speed_pu:begin=None
                elif begin is None:begin=float(moment)
                if begin is not None and moment-begin>=w.dwell_s-1e-12:confirmed=float(moment);break
            delay=None if confirmed is None else confirmed-w.from_s
            checks['sampled_recovery']=delay is not None and delay<=w.maximum_delay_s+1e-12
            metrics.update(sampled_recovery_confirmed_s=confirmed,sampled_recovery_delay_s=delay)
        rows[o.motor_id]=dict(metrics=metrics,checks={k:bool(v) for k,v in checks.items()},within_limits=bool(all(checks.values())));all_checks.extend(checks.values())
    topology=compile_topology(case.study.network);windows=[]
    for w in case.voltage_windows:
        v=channel(topology.node_to_bus[w.node_id]+'.v');
        if np.any(v<0):raise ValueError('negative voltage magnitude')
        selected=(t>=w.from_s)&(t<=w.to_s)
        if w.start_side=='right':selected[abs(t-w.from_s)<=1e-12]&=keep[abs(t-w.from_s)<=1e-12]
        if not selected.any():raise ValueError('empty voltage acceptance window')
        lo=float(min(v[selected]));hi=float(max(v[selected]));passed=lo>=w.minimum_voltage_pu and hi<=w.maximum_voltage_pu
        windows.append(dict(start_side=w.start_side,node_id=w.node_id,from_s=w.from_s,to_s=w.to_s,minimum_sampled_voltage_pu=lo,maximum_sampled_voltage_pu=hi,within_limits=passed));all_checks.append(passed)
    for key in ['maximum_kcl_residual_pu','maximum_power_balance_residual_pu']:
        value=result.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('missing/invalid physical balance: '+key)
        all_checks.append(value<=1e-8)
    return dict(within_limits=bool(all(all_checks)),motors=rows,voltage_windows=windows,scope='sampled balanced RMS criteria; no instantaneous EMT peak or manufacturer temperature claim')


def run_pack(pack,out,*,cancelled=None):
    from power_grid_env.rms_process import run_isolated_rms
    pack=IndustrialDOLPack.model_validate(pack.model_dump(mode='json') if isinstance(pack,IndustrialDOLPack) else pack)
    if len(encode(pack.model_dump(mode='json')))>32000000:raise ValueError('industrial DOL input byte budget')
    reserved=512000
    if len(encode(pack.model_dump(mode='json')))>pack.maximum_output_bytes-reserved:raise ValueError('industrial DOL input exceeds artifact budget')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);start=time.monotonic();code=package_fingerprint();rows=[];used=0
    def write(name,value):
        nonlocal used
        data=encode(value)
        if used+len(data)>pack.maximum_output_bytes-reserved:raise ValueError('industrial DOL artifact byte budget exceeded')
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);used+=len(data)
        return hashlib.sha256(data).hexdigest()
    write('pack.json',pack.model_dump(mode='json'));write('progress.json',dict(status='running',code_sha256=code))
    failure=None
    for case in pack.cases:
        left=pack.total_timeout_s-(time.monotonic()-start)
        if (cancelled is not None and cancelled()) or left<=0:
            failure='cancelled' if cancelled is not None and cancelled() else 'total_timeout';rows.append(dict(id=case.id,status='not_run',expectation_met=False,engineering_acceptable=False,reason=failure));continue
        if package_fingerprint()!=code:failure='source_changed';rows.append(dict(id=case.id,status='not_run',expectation_met=False,engineering_acceptable=False,reason=failure));continue
        try:
            input_sha=write(case.id+'/study.json',case.study.model_dump(mode='json'))
            result=run_isolated_rms(case.study,timeout_s=min(pack.per_case_timeout_s,left),cancelled=cancelled,max_result_bytes=min(64000000,max(1,pack.maximum_output_bytes-used-reserved)))
            if package_fingerprint()!=code:raise ValueError('producer changed during study')
            output_sha=write(case.id+'/result.json',result)
            if result.get('completed'):
                if result.get('code_sha256')!=code or result.get('network_sha256')!=canonical_hash(case.study.network):raise ValueError('DOL result producer/network identity mismatch')
                actual=assessment(case,result);acceptable=actual['within_limits'];met=acceptable if case.expectation=='within_limits' else not acceptable if case.expectation=='violates_limits' else False
                row=dict(id=case.id,status='completed',engineering_acceptable=acceptable,expectation_met=bool(met),assessment=actual)
            else:
                met=case.expectation=='rejected' and result.get('status')=='rejected' and result.get('reason')==case.rejection_reason
                row=dict(id=case.id,status=result.get('status','failed'),reason=result.get('reason'),engineering_acceptable=False,expectation_met=bool(met))
            row.update(input_sha256=input_sha,result_sha256=output_sha,study_sha256=canonical_hash(case.study),parameter_provenance=case.parameter_provenance);assessment_sha=write(case.id+'/assessment.json',row)
            row.pop('assessment',None);row.pop('parameter_provenance',None);row.update(assessment_path=case.id+'/assessment.json',assessment_sha256=assessment_sha);rows.append(row)
        except (ValueError,OSError,RuntimeError) as exc:
            failure=str(exc).encode('utf8')[:4096].decode('utf8',errors='replace');rows.append(dict(id=case.id,status='failed',reason=failure,expectation_met=False,engineering_acceptable=False))
    complete=len(rows)==len(pack.cases) and all(r['status']=='completed' or (c.expectation=='rejected' and r['status']=='rejected' and r['expectation_met']) for c,r in zip(pack.cases,rows))
    if time.monotonic()-start>=pack.total_timeout_s:complete=False
    result=dict(schema='power-grid-env.industrial-dol-result/v1',id=pack.id,purpose=pack.purpose,completed=bool(complete),validation_passed=bool(complete and all(r['expectation_met'] for r in rows)),engineering_acceptable=bool(complete and all(r['engineering_acceptable'] for r in rows)),code_sha256=code,source_unchanged=package_fingerprint()==code,cases=rows,engineering_acceptable_case_ids=[r['id'] for r in rows if r['engineering_acceptable']],scope='declared balanced DOL/restart sampled study pack',site_or_OEM_approval=False,elapsed_s=time.monotonic()-start)
    if not result['source_unchanged']:result.update(completed=False,validation_passed=False,engineering_acceptable=False)
    # Reserve final metadata independently of the large trajectory budget.
    progress=encode(dict(status='complete' if result['completed'] else 'failed',code_sha256=code))
    used-=len((out/'progress.json').read_bytes());(out/'progress.json').write_bytes(progress);used+=len(progress)
    summary=encode(result)
    (out/'summary.json').write_bytes(summary);used+=len(summary)
    lines=['# Industrial DOL study pack',f"Purpose: {pack.purpose}",f"Completed: {result['completed']}",f"Engineering acceptable: {result['engineering_acceptable']}",f"Regression expectations met: {result['validation_passed']}",'']
    lines.extend(f"- {r['id']}: {r['status']}; engineering={r['engineering_acceptable']}; expectation={r['expectation_met']}" for r in rows)
    markdown=('\n'.join(lines)+'\n').encode('utf8');(out/'summary.md').write_bytes(markdown);used+=len(markdown)
    manifest={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    data=encode(manifest)
    if used+len(data)>pack.maximum_output_bytes:raise ValueError('industrial DOL final metadata byte budget')
    (out/'manifest.json').write_bytes(data);return result

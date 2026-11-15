"""Distinguish grid isolation from extinction of a motor-fed RMS fault.

Acceptance is bounded to an owner-specified study and sampled extinction with
explicit event sides. It is not proof of continuous arc extinction or EMT duty.
"""
from pathlib import Path
import math

from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract,Source
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.relay_clock_audit import audit_clock
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash


class ClearanceRequirements(Contract):
    id: str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    source: Source
    fault_id: str
    grid_breaker_id: str
    expected_trip_relays: list[str]=Field(min_length=1,max_length=32)
    allowed_additional_trip_relays: list[str]=Field(default_factory=list,max_length=32)
    maximum_grid_isolation_s: float=Field(gt=0,le=2)
    maximum_fault_extinction_s: float=Field(gt=0,le=2)
    extinction_current_a: float=Field(gt=0,le=10)
    minimum_extinction_observation_s: float=Field(gt=0,le=1)
    minimum_prefault_speed_pu: float=Field(gt=0,le=1)
    require_motor_disconnection: bool
    maximum_sample_step_s: float=Field(gt=0,le=.01)

    @model_validator(mode='after')
    def unique(self):
        combined=self.expected_trip_relays+self.allowed_additional_trip_relays
        if len(combined)!=len(set(combined)):raise ValueError('duplicate expected/allowed relay')
        return self


def assess(study,report,requirements):
    study=StudySpec.model_validate(study);r=ClearanceRequirements.model_validate(requirements)
    s=study.coupled
    if s is None or s.network_automation is None or s.machines:
        raise ValueError('motor-only coupled study with explicit automation required')
    if not report.get('completed') or not report.get('solved'):
        raise ValueError('completed solved report required')
    if len(s.faults)!=1 or s.faults[0].id!=r.fault_id:
        raise ValueError('exactly one owner fault required')
    if report.get('code_sha256')!=package_fingerprint() or report.get('network_sha256')!=canonical_hash(study.network) or report.get('settings')!=s.model_dump(mode='json') or report.get('study_id')!=study.id:
        raise ValueError('trace producer/network/settings identity mismatch')
    fault=s.faults[0];start,end=fault.apply_s,fault.clear_s
    if end-start<=r.maximum_fault_extinction_s+r.minimum_extinction_observation_s:
        raise ValueError('scheduled fault removal masks required extinction observation')
    breakers={b.id:b for b in s.network_automation.breakers}
    if r.grid_breaker_id not in breakers:raise ValueError('unknown owner grid breaker')
    b=breakers[r.grid_breaker_id]
    if b.target_kind not in ('line','source'):raise ValueError('grid isolation requires line/source actuator')
    relays={p.id:p for p in s.network_automation.relays}
    if any(k not in relays for k in r.expected_trip_relays+r.allowed_additional_trip_relays):
        raise ValueError('unknown owner relay')
    times=report['time_s'];sides=report['sample_side'];ch=report['channels']
    if len(times)<2 or len(times)!=len(sides) or any(not math.isfinite(t) for t in times) or any(b<a for a,b in zip(times,times[1:])):
        raise ValueError('invalid event-sided time axis')
    if set(sides)-{'initial','regular','pre_event','post_event'}:raise ValueError('unknown event side')
    keys=[fault.id+'.current_a',fault.id+'.active',b.id+'.closed',study.network.sources[0].id+'.connected']
    keys += [m.id+suffix for m in s.motors for suffix in ['.connected','.speed_pu']]
    if b.target_kind=='line':keys.append(b.target_id+'.from_current_a')
    for key in keys:
        values=ch[key]
        if len(values)!=len(times) or any(not math.isfinite(x) for x in values):raise ValueError('invalid channel '+key)
        if key.endswith(('.active','.closed','.connected')) and any(v not in (0,1) for v in values):raise ValueError('nonbinary channel '+key)
    if any(v<0 for v in ch[fault.id+'.current_a']):raise ValueError('negative fault RMS current')
    for t in [start,end]:
        indices=[i for i,x in enumerate(times) if abs(x-t)<=1e-10]
        if len(indices)!=2 or [sides[i] for i in indices]!=['pre_event','post_event']:
            raise ValueError('fault event sides missing')
    pre=next(i for i,t in enumerate(times) if abs(t-start)<=1e-10 and sides[i]=='pre_event')
    active=[i for i,v in enumerate(ch[fault.id+'.active']) if v==1]
    if not active or times[active[0]]!=start or times[active[-1]]!=end:
        raise ValueError('active fault does not cover owner window')
    if any(ch[fault.id+'.active'][i]!=1 for i,t in enumerate(times) if start<t<end):raise ValueError('fault deactivated inside owner window')
    sample_gap=max((times[j]-times[i] for i,j in zip(active,active[1:])),default=0)
    trips=[e for e in report['event_log'] if e['kind']=='network_relay_trip']
    required=r.expected_trip_relays;allowed=set(required+r.allowed_additional_trip_relays)
    trip_checks=all(sum(e.get('equipment_id')==id and start<=e['time_s']<end for e in trips)==1 for id in required)
    no_extra=all(e.get('equipment_id') in allowed and start<=e['time_s']<end for e in trips)
    openings=[e for e in report['event_log'] if e['kind']=='breaker_open' and e.get('equipment_id')==b.id and e.get('outcome')=='applied']
    isolated=None;isolation_ok=False
    if len(openings)==1 and start<=openings[0]['time_s']<end:
        isolated=openings[0]['time_s']
        after=[i for i,t in enumerate(times) if t>isolated+1e-10 or abs(t-isolated)<=1e-10 and sides[i]!='pre_event']
        isolation_ok=bool(after) and all(ch[b.id+'.closed'][i]==0 for i in after)
        key=b.target_id+'.from_current_a' if b.target_kind=='line' else study.network.sources[0].id+'.connected'
        isolation_ok &= all(abs(ch[key][i])<=1e-7 for i in after)
    # Earliest sample after which EVERY active-fault sample remains below the
    # threshold. Do not accept a single transient dip or fault-clear jump.
    extinction=None
    suffix=True
    for i in reversed(active):
        suffix &= ch[fault.id+'.current_a'][i]<=r.extinction_current_a
        if suffix:extinction=times[i]
    observation=0 if extinction is None else end-extinction
    motor_off=False
    if extinction is not None:
        after=[i for i in active if times[i]>extinction+1e-10 or abs(times[i]-extinction)<=1e-10 and sides[i]!='pre_event']
        motor_off=bool(after) and all(ch[m.id+'.connected'][i]==0 for m in s.motors for i in after)
    applied=[e for e in report['event_log'] if e['kind']=='breaker_open' and e.get('outcome')=='applied']
    actuator_timing=True
    for event in [e for e in report['event_log'] if e['kind']=='breaker_open' and e.get('outcome') in ('applied','failed_to_operate')]:
        id=event.get('equipment_id')
        if id not in breakers:
            actuator_timing=False;continue
        requests=[e for e in report['event_log'] if e['kind']=='breaker_open_requested' and e.get('equipment_id')==id and abs(e.get('due_s',-1)-event['time_s'])<=1e-8 and abs(e['time_s']+breakers[id].opening_delay_s-event['time_s'])<=1e-8]
        actuator_timing &= bool(requests)
    opening_sides=False
    if isolated is not None:
        transition=[i for i,t in enumerate(times) if abs(t-isolated)<=1e-10]
        opening_sides=len(transition)==2 and [sides[i] for i in transition]==['pre_event','post_event'] and [ch[b.id+'.closed'][i] for i in transition]==[1,0]
    clocks={id:audit_clock(report,p) for id,p in relays.items()}
    checks=dict(prefault_running=bool(s.motors) and all(ch[m.id+'.connected'][pre]==1 and ch[m.id+'.speed_pu'][pre]>=r.minimum_prefault_speed_pu for m in s.motors),
                expected_relays=trip_checks,no_unexpected_trip=no_extra,
                grid_electrically_isolated=isolation_ok,
                exact_open_actuators=len(applied)==1 and applied[0].get('equipment_id')==b.id,
                actuator_delay_audit=actuator_timing,opening_event_sides=opening_sides,
                grid_isolation_deadline=isolated is not None and isolated-start<=r.maximum_grid_isolation_s+1e-8,
                fault_extinction_deadline=extinction is not None and extinction-start<=r.maximum_fault_extinction_s+1e-8,
                sustained_extinction_observation=observation>=r.minimum_extinction_observation_s-1e-8,
                motors_disconnected=not r.require_motor_disconnection or motor_off,
                clock_audits=all(a['passed'] for a in clocks.values()),
                sample_resolution=sample_gap<=r.maximum_sample_step_s+1e-10)
    tail=[i for i in active if isolated is not None and (times[i]>isolated+1e-10 or abs(times[i]-isolated)<=1e-10 and sides[i]!='pre_event')]
    result=dict(schema='power-grid-env.running-fault-protection/v1',code_sha256=package_fingerprint(),
        study_sha256=digest(encoded(study.model_dump(mode='json'))),requirements_sha256=digest(encoded(r.model_dump(mode='json'))),
        report_sha256=digest(encoded(report)),checks=checks,passed=all(checks.values()),clock_audits=clocks,
        grid_isolation_time_s=isolated,fault_extinction_time_s=extinction,
        fault_extinction_observation_s=observation,
        maximum_sampled_fault_current_after_grid_isolation_a=max((ch[fault.id+'.current_a'][i] for i in tail),default=None),
        maximum_active_sample_gap_s=sample_gap,normative_approval=False,reward=None,
        scope='Owner-bounded sampled RMS fault extinction distinct from line/source isolation, running motor precondition, physical actuator states and replayed relay clocks. No arc/DC/EMT, continuous intersample extinction guarantee or general selectivity approval.')
    encoded(result);return result


def run(study,requirements,out):
    study=StudySpec.model_validate(study).model_dump(mode='json');requirements=ClearanceRequirements.model_validate(requirements).model_dump(mode='json')
    report=run_study(StudySpec.model_validate(study));result=assess(study,report,requirements)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,value in [('study',study),('requirements',requirements),('trace',report),('result',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result


def verify_clearance(root,*,expected_study,expected_requirements):
    root=Path(root);before=verify(root)
    study=StudySpec.model_validate(expected_study).model_dump(mode='json')
    req=ClearanceRequirements.model_validate(expected_requirements).model_dump(mode='json')
    if read_json(root/'study.json')!=study or read_json(root/'requirements.json')!=req:raise ValueError('owner inputs mismatch')
    result=assess(study,read_json(root/'trace.json'),req)
    if result!=read_json(root/'result.json'):raise ValueError('clearance result mismatch')
    if verify(root)!=before:raise ValueError('artifact changed')
    return dict(verified=True,manifest_sha256=before,**result)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',required=True);p.add_argument('--requirements',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=run(read_json(a.study),read_json(a.requirements),a.out)
    print(encoded(r).decode())
    if not r['passed']:raise SystemExit(2)

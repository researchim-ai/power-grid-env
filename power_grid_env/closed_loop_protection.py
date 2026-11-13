"""Nominal motor-only radial protection actions, with full three-grid evidence."""
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract,Selection
from power_grid_env.equipment_design import EquipmentDesignTask,compile_equipment
from power_grid_env.design_study import compile_design
from power_grid_env.protection_design import RelaySetting
from power_grid_env.coupled_rms import CoupledSettings
from power_grid_env.industrial_dol import IndustrialDOLCase,MotorDOLCriteria,DOLVoltageWindow
from power_grid_env.motor_engineering import MotorEngineeringPlan,run_engineering,verify_engineering
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify


class ClosedLoopPlan(Contract):
    schema_version: Literal['power-grid-env.closed-loop-protection/v1']='power-grid-env.closed-loop-protection/v1'
    id: str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    equipment: EquipmentDesignTask
    selection: Selection
    primary: RelaySetting
    backup: RelaySetting
    integration_method: Literal['radau','network_dae']='radau'
    clock_policy: Literal['definite_analytic','sampled_integral']='definite_analytic'
    fault_node_id: str
    fault_apply_s: float=Field(gt=0)
    fault_clear_s: float=Field(gt=0)
    fault_duration_s: float=Field(gt=0,le=10)
    fault_r_ohm: float=Field(gt=0)
    breaker_failure_delay_s: float=Field(gt=0,le=1)
    breaker_failure_current_a: float=Field(gt=0)
    fault_motors: list[MotorDOLCriteria]=Field(min_length=1,max_length=8)
    fault_voltage_windows: list[DOLVoltageWindow]=Field(min_length=1,max_length=64)
    event_time_tolerance_s: float=Field(default=1e-7,gt=0,le=1e-5)

    @model_validator(mode='after')
    def applicable(self):
        if self.clock_policy=='definite_analytic' and (self.primary.curve!='definite' or self.backup.curve!='definite'):
            raise ValueError('closed-loop v1 requires definite-time stages')
        if not self.fault_apply_s<self.fault_clear_s<self.fault_duration_s:
            raise ValueError('fault apply/clear must lie inside horizon')
        latest=self.fault_apply_s+max(self.primary.time_multiplier+self.primary.breaker_opening_s+self.breaker_failure_delay_s+self.backup.breaker_opening_s,
                                     self.backup.time_multiplier+self.backup.breaker_opening_s)
        if self.primary.curve==self.backup.curve=='definite' and latest>=self.fault_clear_s:raise ValueError('fault must persist beyond both declared clearing paths')
        if len(self.equipment.mandatory_cases)>5:raise ValueError('five normal cases plus three fault cases maximum')
        return self


def compile_closed_loop(plan):
    plan=ClosedLoopPlan.model_validate(plan)
    _,_,static,feeder=compile_equipment(plan.equipment,plan.selection)
    _,_,base=compile_design(feeder,dict(option_id='selected'))
    n=base.cases[0].study.network
    terminals={t.id:t.connectivity_node_id for t in n.terminals}
    upstream=terminals[n.sources[0].terminal_id]
    downstream=next(node.id for node in n.connectivity_nodes if node.id!=upstream)
    if plan.fault_node_id!=downstream:raise ValueError('fault must be at downstream motor bus')
    if any(c.study.induction.operating_point is not None for c in base.cases):raise ValueError('cold motor initialization required')
    reserved={'primary-cb','backup-cb','primary-oc','backup-oc','protection-fault'}
    if reserved & {x.id for group in (n.lines,n.sources,n.loads,n.connectivity_nodes) for x in group}:raise ValueError('reserved protection ID collision')
    if reserved & {m.id for m in base.cases[0].study.induction.motors}:raise ValueError('reserved protection motor ID collision')
    cases=[];expectations={}
    def protect(case,kind):
        p=case.model_dump(mode='json');s=p['study'];cfg=s['induction'];s['induction']=None;s['study']='coupled_rms'
        cfg.update(machines=[],integration_method=plan.integration_method,jacobian_network='direct_solve',jacobian_format='csc',event_policy='coincident_roots_v1')
        breakers=[dict(id='primary-cb',target_kind='line',target_id=n.lines[0].id,opening_delay_s=plan.primary.breaker_opening_s,
            fail_open=kind=='breaker_failure',breaker_failure_delay_s=plan.breaker_failure_delay_s,
            breaker_failure_current_a=plan.breaker_failure_current_a,backup_breaker_ids=['backup-cb'] if kind=='breaker_failure' else []),
            dict(id='backup-cb',target_kind='source',target_id=n.sources[0].id,opening_delay_s=plan.backup.breaker_opening_s)]
        relays=[dict(id=id,kind='overcurrent',measurement_kind=measurement,measurement_id=target,breaker_id=breaker,
            pickup=setting.pickup_a,curve=setting.curve,time_multiplier_s=setting.time_multiplier,measurement_available=True)
            for id,measurement,target,breaker,setting in [('primary-oc','line',n.lines[0].id,'primary-cb',plan.primary),('backup-oc','source',n.sources[0].id,'backup-cb',plan.backup)]]
        cfg['network_automation']=dict(allow_islands=True,breakers=breakers,relays=relays)
        if kind!='normal':
            cfg.update(duration_s=plan.fault_duration_s,events=[],faults=[dict(id='protection-fault',node_id=plan.fault_node_id,
                apply_s=plan.fault_apply_s,clear_s=plan.fault_clear_s,r_ohm=plan.fault_r_ohm,x_ohm=0)])
            p.update(id='fault-'+kind,motors=[m.model_dump(mode='json') for m in plan.fault_motors],voltage_windows=[w.model_dump(mode='json') for w in plan.fault_voltage_windows])
            if kind=='measurement_loss':cfg['events']=[dict(time_s=plan.fault_apply_s/2,kind='relay_measurement_lost',equipment_id='primary-oc')]
        cfg=CoupledSettings.model_validate(cfg);s['coupled']=cfg.model_dump(mode='json')
        p['parameter_provenance']+='\nExplicit nominal relay parameters; uncertainty requires separately enumerated effective plans.'
        protected=IndustrialDOLCase.model_validate(p);cases.append(protected);expectations[protected.id]=kind
    for case in base.cases:protect(case,'normal')
    for kind in ['primary','breaker_failure','measurement_loss']:protect(base.cases[0],kind)
    pack=MotorEngineeringPlan(id=plan.id,cases=cases,per_run_timeout_s=90,total_timeout_s=1200)
    return plan,pack,expectations,static


def action_check(plan,kind,report):
    events=report['event_log'];required=[];forbidden=[];t=plan.fault_apply_s
    def trip(id,setting):
        if plan.clock_policy=='definite_analytic':return t+setting.time_multiplier
        rows=[e for e in events if e['kind']=='network_relay_trip' and e.get('equipment_id')==id]
        return rows[0]['time_s'] if len(rows)==1 and t<=rows[0]['time_s']<plan.fault_clear_s else None
    primary_trip=trip('primary-oc',plan.primary);backup_trip=trip('backup-oc',plan.backup)
    def later(at,delay):return None if at is None else at+delay
    if kind=='normal':forbidden=['primary-oc','backup-oc']
    elif kind=='primary':
        required=[('network_relay_trip','primary-oc',None,primary_trip),
            ('breaker_open','primary-cb','applied',later(primary_trip,plan.primary.breaker_opening_s))]
        forbidden=['backup-oc']
    elif kind=='breaker_failure':
        failed=later(primary_trip,plan.primary.breaker_opening_s)
        required=[('breaker_open','primary-cb','failed_to_operate',failed),
            ('breaker_failure_confirmed','primary-cb',None,later(failed,plan.breaker_failure_delay_s)),
            ('breaker_open','backup-cb','applied',later(failed,plan.breaker_failure_delay_s+plan.backup.breaker_opening_s))]
    else:
        required=[('relay_measurement_lost','primary-oc','applied',t/2),
            ('network_relay_trip','backup-oc',None,backup_trip),
            ('breaker_open','backup-cb','applied',later(backup_trip,plan.backup.breaker_opening_s))]
        forbidden=['primary-oc']
    checks={}
    for event_kind,equipment,outcome,at in required:
        found=[e for e in events if e['kind']==event_kind and e.get('equipment_id')==equipment and (outcome is None or e.get('outcome')==outcome)]
        checks[event_kind+':'+equipment]=at is not None and len(found)==1 and abs(found[0]['time_s']-at)<=plan.event_time_tolerance_s
    checks['no_forbidden_relay_trip']=not any(e['kind']=='network_relay_trip' and e.get('equipment_id') in forbidden for e in events)
    applied=[e for e in events if e['kind']=='breaker_open' and e.get('outcome')=='applied']
    checks['exact_open_actions']=sorted(e['equipment_id'] for e in applied)==([] if kind=='normal' else ['primary-cb'] if kind=='primary' else ['backup-cb'])
    # Events must also agree with sampled actuator states and actual line/source
    # isolation, rather than accepting a plausible event log alone.
    for event in applied:
        cb=event['equipment_id'];after=[i for i,time in enumerate(report['time_s']) if time>=event['time_s'] and report['sample_side'][i]!='pre_event']
        checks[cb+':open_state']=bool(after) and all(report['channels'][cb+'.closed'][i]==0 for i in after)
        n=plan.equipment.mandatory_cases[0].study.network
        key=n.lines[0].id+'.from_current_a' if cb=='primary-cb' else n.sources[0].id+'.connected'
        checks[cb+':electrical_isolation']=bool(after) and all(abs(report['channels'][key][i])<=1e-7 for i in after)
    return dict(passed=all(checks.values()),checks=checks,required_event_count=len(required))


def verdict(plan,expectations,static,root,summary):
    rows=[]
    for id,kind in expectations.items():
        grids=[]
        for level in range(3):
            path=Path(root)/id/f'grid-{level}'/'result.json'
            if not path.exists():break
            report=read_json(path)
            if not report.get('completed'):break
            row=action_check(plan,kind,report)
            if plan.clock_policy=='sampled_integral':
                from power_grid_env.relay_clock_audit import audit_clock
                from power_grid_env.study_spec import StudySpec
                study=StudySpec.model_validate(read_json(path.parent/'study.json'))
                row['clock_audits']={r.id:audit_clock(report,r) for r in study.coupled.network_automation.relays}
                row['passed']=row['passed'] and all(a['passed'] for a in row['clock_audits'].values())
            grids.append(row)
        rows.append(dict(id=id,kind=kind,grids=grids,passed=len(grids)==3 and all(r['passed'] for r in grids)))
    qualified=summary['completed'] and all(c['numerically_qualified'] for c in summary['cases'])
    passed=qualified and summary['engineering_acceptable'] and static['passes_declared_checks'] and all(r['passed'] for r in rows)
    return dict(schema='power-grid-env.closed-loop-protection-result/v1',plan_sha256=digest(encoded(plan.model_dump(mode='json'))),
        completed=summary['completed'],numerically_qualified=bool(qualified),passes_declared_profile=bool(passed),actions=rows,
        static_checks=static['checks'],code_sha256=summary['producer_sha256'],reward=None,normative_approval=False,
        scope='explicit nominal relay parameters, '+plan.clock_policy+' clock audit; radial motor-only cold-start profile; actual relay clocks, breaker actions/failure and residual-flux coast; three grids; no uncertainty envelope, OEM curves, cold/hot fault bounds or general selectivity claim')


def run_closed_loop(plan,out,*,cancelled=None):
    plan,pack,expectations,static=compile_closed_loop(plan);root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'plan.json').write_bytes(encoded(plan.model_dump(mode='json')))
    summary=run_engineering(pack,root/'studies',cancelled=cancelled)
    result=verdict(plan,expectations,static,root/'studies',summary)
    (root/'result.json').write_bytes(encoded(result));seal(root);return result


def verify_closed_loop(root,*,expected_plan=None):
    root=Path(root);before=verify(root);plan,pack,expectations,static=compile_closed_loop(read_json(root/'plan.json'))
    if expected_plan is not None and ClosedLoopPlan.model_validate(expected_plan)!=plan:raise ValueError('owner closed-loop plan mismatch')
    if read_json(root/'studies'/'input.json')!=pack.model_dump(mode='json'):raise ValueError('protected study compilation differs')
    verify_engineering(root/'studies')
    result=verdict(plan,expectations,static,root/'studies',read_json(root/'studies'/'summary.json'))
    if read_json(root/'result.json')!=result:raise ValueError('protected evidence differs')
    if verify(root)!=before:raise ValueError('protected study changed')
    return dict(verified=True,manifest_sha256=before,owner_plan_anchored=expected_plan is not None,**result)

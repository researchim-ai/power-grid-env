"""Bounded, immutable local engineering campaigns and verified continuation.

Only explicitly typed JSON studies run in fresh processes. Continuation copies
verified finished attempts into a NEW self-contained run; partial hard-crash
folders are never trusted as a completed campaign.
"""
from pathlib import Path
from typing import Literal,Any
import copy,csv,html,itertools,math
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.study_artifacts import read_json,encoded,digest,verify
from power_grid_env.report import write_json
from power_grid_env.evidence import package_fingerprint
from power_grid_env.paths import package_dir


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,strict=True)


def pointer(document,path):
    from power_grid_env.study_sensitivity import _tokens,_index
    node=document
    for token in _tokens(path):node=node[_index(node,token)]
    return node


class Rule(StrictModel):
    path:str
    operator:Literal['eq','le','ge']
    value:Any

    @model_validator(mode='after')
    def valid(self):
        from power_grid_env.study_sensitivity import _tokens
        _tokens(self.path)
        if self.value is not None and type(self.value) not in (str,bool,int,float):raise ValueError('scalar rule value required')
        if type(self.value) in (int,float) and not math.isfinite(self.value):raise ValueError('finite rule value required')
        if self.operator!='eq' and type(self.value) not in (int,float):raise ValueError('ordered rule requires a numeric limit')
        return self


class CampaignCase(StrictModel):
    id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    kind:Literal['protected_motor','study','sensitivity','dynamic_motor_identification','dynamic_identification_audit','anchored_dynamic_identification']
    study:dict
    protocol:dict|None=None
    rules:list[Rule]=Field(default_factory=list,max_length=64)
    parameters:dict[str,float]=Field(default_factory=dict,max_length=8)
    worker_wall_s:float=Field(default=120.,gt=0,le=600.)
    memory_limit_mb:int=Field(default=4096,ge=256,le=8192)

    @model_validator(mode='after')
    def physical(self):
        if self.kind=='protected_motor':
            from power_grid_env.protected_motor import ProtectedMotorStudy
            self.study=ProtectedMotorStudy.model_validate(self.study).model_dump(mode='json')
        elif self.kind=='anchored_dynamic_identification':
            from power_grid_env.stator_resistance_anchor import AnchoredDynamicIdentification
            self.study=AnchoredDynamicIdentification.model_validate(self.study).model_dump(mode='json')
        elif self.kind=='dynamic_identification_audit':
            from power_grid_env.dynamic_identification_audit import DynamicIdentificationAudit
            self.study=DynamicIdentificationAudit.model_validate(self.study).model_dump(mode='json')
        elif self.kind=='dynamic_motor_identification':
            from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification
            self.study=DynamicMotorIdentification.model_validate(self.study).model_dump(mode='json')
        else:
            from power_grid_env.study_spec import StudySpec
            from power_grid_env.engineering_project import check_spec
            spec=StudySpec.model_validate(self.study)
            if spec.study not in {'motor_start','coupled_rms','flux_network'}:raise ValueError('campaign supports motor_start, coupled_rms and flux_network studies')
            checked=check_spec(spec)
            if not checked['ok']:raise ValueError('study preflight: '+str(checked['errors']))
            self.study=spec.model_dump(mode='json')
        if self.kind=='sensitivity':
            from power_grid_env.study_sensitivity import SensitivityRequest
            if self.protocol is None:raise ValueError('sensitivity protocol required')
            request=SensitivityRequest.model_validate(self.protocol)
            if request.isolate:raise ValueError('campaign already isolates the worker; set sensitivity protocol isolate=false')
            self.protocol=request.model_dump(mode='json')
        elif self.protocol is not None:raise ValueError('protocol only applies to sensitivity cases')
        if len(encoded(self.model_dump(mode='json')))>2_000_000:raise ValueError('case input byte budget exceeded')
        return self


class CampaignPlan(StrictModel):
    schema_version:Literal['power-grid-env.campaign/v1']='power-grid-env.campaign/v1'
    id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    cases:list[CampaignCase]=Field(min_length=1,max_length=64)
    maximum_wall_s:float=Field(default=1800.,gt=0,le=3600.)
    stop_on_failure:bool=False
    maximum_workers:int=Field(default=1,ge=1,le=8)
    memory_budget_mb:int=Field(default=8192,ge=256,le=65536)

    @model_validator(mode='after')
    def unique(self):
        if any(c.memory_limit_mb>self.memory_budget_mb for c in self.cases):raise ValueError('a case exceeds the campaign memory reservation budget')
        if len({c.id for c in self.cases})!=len(self.cases):raise ValueError('unique case IDs required')
        if len(encoded(self.model_dump(mode='json')))>16_000_000:raise ValueError('campaign input byte budget exceeded')
        return self


class GridAxis(StrictModel):
    path:str
    values:list[float]=Field(min_length=1,max_length=16)
    unit:str=Field(min_length=1,max_length=64)


class CampaignGrid(StrictModel):
    id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    template:CampaignCase
    axes:list[GridAxis]=Field(min_length=1,max_length=4)

    @model_validator(mode='after')
    def bounded(self):
        if len({a.path for a in self.axes})!=len(self.axes):raise ValueError('unique axis paths required')
        if math.prod(len(a.values) for a in self.axes)>64:raise ValueError('grid exceeds 64 cases')
        for a in self.axes:
            if len(set(a.values))!=len(a.values):raise ValueError('unique axis values required')
            value=pointer(self.template.study,a.path)
            if type(value) not in (int,float):raise ValueError('grid requires numeric study leaves')
        # Overlapping leaves cannot occur, but ancestor paths must be rejected
        # before expansion rather than depending on assignment order.
        if any(a.path.startswith(b.path+'/') for a in self.axes for b in self.axes if a is not b):raise ValueError('overlapping grid axes')
        return self


def expand_grid(grid):
    grid=CampaignGrid.model_validate(grid);cases=[]
    from power_grid_env.study_sensitivity import _locate
    for index,values in enumerate(itertools.product(*(a.values for a in grid.axes))):
        row=copy.deepcopy(grid.template.model_dump(mode='json'));row['id']='case-'+str(index).zfill(3)
        for axis,value in zip(grid.axes,values):
            node,key=_locate(row['study'],axis.path);node[key]=value
            row['parameters'][axis.path+' ['+axis.unit+']']=value
        cases.append(CampaignCase.model_validate(row))
    return CampaignPlan(id=grid.id,cases=cases)


def assessment(case,result):
    if not isinstance(result,dict):raise ValueError('result must be a JSON object')
    rows=[]
    for rule in case.rules:
        try:
            value=pointer(result,rule.path)
            if value is not None and type(value) not in (str,bool,int,float):raise ValueError('scalar metric required')
            numeric=type(value) in (int,float) and math.isfinite(value)
            passed=(type(value) is type(rule.value) and value==rule.value) if rule.operator=='eq' else numeric and (value<=rule.value if rule.operator=='le' else value>=rule.value)
            # Numeric JSON equality accepts integer/float, never bool==1.
            if rule.operator=='eq' and numeric and type(rule.value) in (int,float):passed=value==rule.value
            rows.append(dict(path=rule.path,operator=rule.operator,limit=rule.value,actual=value,passed=bool(passed)))
        except (ValueError,KeyError,IndexError,TypeError):rows.append(dict(path=rule.path,operator=rule.operator,limit=rule.value,actual=None,passed=False,reason='missing_or_invalid_metric'))
    completed=result.get('completed') is True and result.get('solved') is not False
    available=(result.get('available') is True if case.kind=='sensitivity' else result.get('passed') is True and result.get('identifiable') is True if case.kind in {'dynamic_motor_identification','dynamic_identification_audit','anchored_dynamic_identification'} else True)
    if case.kind=='dynamic_identification_audit':available=available and result.get('fit_passed') is True and result.get('raw_residuals_passed') is True
    if case.kind=='anchored_dynamic_identification':
        cases=result.get('cases')
        available=available and result.get('total_work_budget_satisfied') is True and result.get('bracket_completed') is True and result.get('skipped_cases')==[] and isinstance(cases,list) and len(cases)==3 and all(isinstance(row,dict) and isinstance(row.get('id'),str) and row.get('passed') is True and row.get('completed') is True and row.get('identifiable') is True for row in cases) and {row.get('id') for row in cases}=={'nominal','lower','upper'}
        if available:
            from power_grid_env.stator_resistance_anchor import resistance_interval
            anchor=resistance_interval(case.study['stator_resistance_measurement'])
            total=result.get('rhs_evaluations')
            available=result.get('anchor')==anchor and type(total) is int and 0<=total<=case.study['maximum_total_rhs_evaluations'] and all(type(row.get('rhs_evaluations')) is int and row['rhs_evaluations']>=0 and type(row.get('resistance_ohm')) in (int,float) and row['resistance_ohm']==anchor[row['id']+'_ohm'] for row in cases)
            if available:available=sum(row['rhs_evaluations'] for row in cases)==total
    return dict(numerically_completed=completed,available=available,passed=bool(completed and available and all(r['passed'] for r in rows)),rules=rows)


def _identity(case):return digest(encoded(case.model_dump(mode='json')))


def _csv(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def _report(out,plan,rows):
    escape=lambda x:html.escape(str(x))
    table=[]
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['case','status','passed','metric','operator','limit','actual'])
        for row in rows:
            tests=row.get('assessment',{}).get('rules',[])
            for test in tests or [dict(path='',operator='',limit='',actual='')]:writer.writerow([_csv(v) for v in [row['id'],row['status'],row['passed'],test['path'],test['operator'],test['limit'],test['actual']]])
            link='attempts/'+row['id']+'/execution.json' if row.get('attempt_manifest_sha256') else 'summary.json'
            table.append('<tr><td><a href="'+link+'">'+escape(row['id'])+'</a></td><td>'+escape(row['status'])+'</td><td>'+escape(row['passed'])+'</td><td>'+escape(row.get('parameters',{}))+'</td><td>'+escape(tests)+'</td></tr>')
    (out/'report.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Серийные исследования</title><style>body{font:16px system-ui;margin:24px;color:#183346}table{border-collapse:collapse;width:100%}td,th{border:1px solid #b8cad7;padding:8px;text-align:left}td{overflow-wrap:anywhere}a{color:#096082}</style><h1>'+escape(plan.id)+'</h1><p>Каждый случай — отдельный процесс. Успех расчёта и выполнение инженерных критериев учитываются отдельно. Пропущенные и незавершённые случаи не засчитываются.</p><table><tr><th>Случай</th><th>Статус</th><th>Критерии выполнены</th><th>Параметры</th><th>Проверки</th></tr>'+''.join(table)+'</table><p><a href="summary.json">Итоги JSON</a> · <a href="comparison.csv">Сравнение CSV</a> · <a href="plan.json">Входные данные</a></p></html>',encoding='utf-8')


def run_campaign(document,out,*,resume_from=None,recover_from=None,cancel_file=None):
    from power_grid_env.campaign_executor import execute
    return execute(document,out,resume_from=resume_from,recover_from=recover_from,cancel_file=cancel_file)


def validate_attempt(root,case,code):
    """One interpretation for fresh, resumed and recovered completed attempts."""
    root=Path(root);manifest=verify(root);execution=read_json(root/'execution.json')
    if not isinstance(execution,dict):raise ValueError('execution must be a JSON object')
    if (execution.get('id')!=case.id or execution.get('case_sha256')!=_identity(case)
            or execution.get('code_sha256')!=code or read_json(root/'case.json')!=case.model_dump(mode='json')):
        raise ValueError('case producer or input differs')
    if execution.get('parameters')!=case.parameters:raise ValueError('case parameter labels differ')
    if type(execution.get('passed')) is not bool:raise ValueError('case acceptance must be boolean')
    if execution.get('status') in {'passed','criteria_failed','calculation_failed'}:
        worker=read_json(root/'worker.json')
        if not isinstance(worker,dict):raise ValueError('worker identity must be a JSON object')
        if worker.get('code_sha256')!=code or worker.get('case_sha256')!=_identity(case):raise ValueError('worker identity differs')
        wanted=assessment(case,read_json(root/'result.json'))
        expected='passed' if wanted['passed'] else 'criteria_failed' if wanted['numerically_completed'] else 'calculation_failed'
        if execution.get('assessment')!=wanted or execution['passed']!=wanted['passed'] or execution['status']!=expected:
            raise ValueError('case acceptance differs from physical result')
    elif (execution.get('status') not in {'worker_error','worker_start_error','supervisor_error','worker_timeout','campaign_timeout','cancelled','output_budget_exceeded','invalid_worker_result'}
            or execution['passed'] or execution.get('assessment')!=dict(numerically_completed=False,available=False,passed=False,rules=[])):
        raise ValueError('unfinished attempt cannot pass')
    return dict(execution,attempt_manifest_sha256=manifest)


def verify_campaign(directory):
    """Verify bytes AND result accounting; SHA-256 alone is not authentication."""
    root=Path(directory);manifest=verify(root)
    document=read_json(root/'plan.json');plan=CampaignPlan.model_validate(document)
    result=read_json(root/'summary.json')
    if not isinstance(result,dict):raise ValueError('summary must be a JSON object')
    if result.get('schema')!='power-grid-env.campaign-result/v1' or result.get('id')!=plan.id or result.get('plan_sha256')!=digest(encoded(document)):raise ValueError('campaign plan identity differs')
    rows=result.get('cases',[])
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('invalid campaign case rows')
    if [r.get('id') for r in rows]!=[c.id for c in plan.cases]:raise ValueError('campaign case inventory/order differs')
    expected_attempts={row['id'] for row in rows if row.get('attempt_manifest_sha256')}
    actual_attempts={p.name for p in (root/'attempts').iterdir()} if (root/'attempts').exists() else set()
    if actual_attempts!=expected_attempts:raise ValueError('unexpected or missing campaign attempt')
    for case,row in zip(plan.cases,rows):
        if type(row.get('passed')) is not bool:raise ValueError('case acceptance must be boolean')
        if not row.get('attempt_manifest_sha256'):
            if row['passed'] or row.get('assessment') is not None or row.get('status') not in {'not_run_cancelled','not_run_campaign_timeout','not_run_stop_on_failure','not_run_output_budget_exceeded'}:raise ValueError('invalid deferred case accounting')
            continue
        actual=validate_attempt(root/'attempts'/case.id,case,result['code_sha256'])
        if any(actual.get(k)!=row.get(k) for k in ('id','status','passed','assessment','case_sha256','code_sha256','attempt_manifest_sha256')):raise ValueError('attempt summary differs')
    if type(result.get('passed')) is not bool or type(result.get('completed')) is not bool:raise ValueError('campaign acceptance must be boolean')
    if 'scheduler' in result or 'maximum_workers' in document:
        from power_grid_env.campaign_executor import verify_scheduler
        verify_scheduler(root,plan,result)
    if result.get('passed')!=all(r['passed'] for r in rows) or result.get('completed')!=all(r.get('assessment',{}).get('numerically_completed',False) for r in rows):raise ValueError('campaign success accounting differs')
    return manifest

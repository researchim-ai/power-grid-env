"""Bounded owner-authored engineering episodes over the existing editor PF bridge.

Worker state is private to the local HTTP server. No client-supplied project,
reward, requirements or simulation result is accepted. Tool/API separation is
not OS isolation: an evaluated model must not read this evaluator's filesystem.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from jsonschema import Draft202012Validator,ValidationError as SchemaError
from benchmarks.grid_editor import Draft,compile_draft,solve
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import seal

FAMILIES={'chain':('train',[-1,0,1]),'star':('dev',[-1,0,0,0]),'branched':('test',[-1,0,0,1,2])}
CATALOG={'copper-1':dict(r_ohm_km=.4,x_ohm_km=.08,rating_a=180.),'copper-2':dict(r_ohm_km=.2,x_ohm_km=.08,rating_a=250.)}
BUGS=('impedance','ampacity','missing_connection')
TOOLS=('inspect_project','inspect_equipment','validate','simulate','add_line','add_load','set_line_catalog','remove_line','submit','export')


def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def family_key(parents):
    def tree(i):return '('+''.join(sorted(tree(j) for j,p in enumerate(parents) if p==i))+')'
    return sha(tree(0))

def registry():
    rows=[]
    for family,(split,parents) in FAMILIES.items():
        for kind,bug in [('assemble',None)]+[(k,b) for k in ('diagnose','repair') for b in BUGS]:
            rows.append(dict(id='task-'+sha(dict(family=family,kind=kind,condition=bug,namespace='engineering-episodes-v1'))[:24],kind=kind,split=split,family=family,family_sha256=family_key(parents),bug=bug,parents=parents))
    return rows


def task(identifier,allow_private=False):
    found=next((t for t in registry() if t['id']==identifier),None)
    if found is None or (found['split']!='train' and not allow_private):raise ValueError('Unknown public training task')
    return found


def requirements(t):
    return dict(source_node='n0',nominal_ll_v=400.,routes=[dict(id='route'+str(i),from_node='n'+str(p),to_node='n'+str(i),length_m=100.) for i,p in enumerate(t['parents']) if i],loads=[dict(id='load'+str(i),node_id='n'+str(i),p_kw=20.,q_kvar=5.) for i in range(1,len(t['parents']))],catalog=copy.deepcopy(CATALOG),minimum_voltage_pu=.95,maximum_branch_loading_pct=100.,all_loads_energized=True)


def initial_project(t):
    req=requirements(t);n=len(t['parents'])
    d=dict(schema='power-grid-env.grid-editor/v5',profile='passive_power_flow',nominal_ll_v=400.,source_node='n0',source_r_ohm=.006,source_x_ohm=.024,duration_s=.2,step_s=.002,nodes=[dict(id='n'+str(i),nominal_ll_v=400.,x=160.+240.*(i%4),y=150.+220.*(i//4)) for i in range(n)],lines=[],transformers=[],switches=[],motors=[],generators=[],loads=[],events=[],fault=dict(node_id='n0',r_ohm=.001,x_ohm=0.))
    if t['kind']!='assemble':
        d['loads']=copy.deepcopy(req['loads'])
        for i,r in enumerate(req['routes'],1):d['lines'].append(dict(id='line'+str(i),from_node=r['from_node'],to_node=r['to_node'],length_m=r['length_m'],**CATALOG['copper-1']))
        if t['bug']=='impedance':d['lines'][0]['r_ohm_km']=12.
        elif t['bug']=='ampacity':d['lines'][0]['rating_a']=1.
        elif t['bug']=='missing_connection':d['lines'].pop(0)
    return Draft.model_validate(d).model_dump(mode='json',by_alias=True)


def tool_schemas(t):
    req=requirements(t);identifier=dict(type='string',pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')
    specs={name:({},[]) for name in TOOLS}
    specs.update(inspect_equipment=({'equipment_id':identifier},['equipment_id']),add_line=({'route_id':dict(enum=[r['id'] for r in req['routes']]),'equipment_id':identifier,'catalog_id':dict(enum=list(CATALOG))},['route_id','equipment_id','catalog_id']),add_load=({'load_id':dict(enum=[l['id'] for l in req['loads']])},['load_id']),set_line_catalog=({'equipment_id':identifier,'catalog_id':dict(enum=list(CATALOG))},['equipment_id','catalog_id']),remove_line=({'equipment_id':identifier},['equipment_id']))
    fields={'explanation':dict(type='string',maxLength=4000)};required=[]
    if t['kind']=='diagnose':fields.update(cause=dict(enum=list(BUGS)),location=identifier);required=['cause','location']
    specs['submit']=(fields,required)
    names=[name for name in TOOLS if t['kind']!='diagnose' or name not in ('add_line','add_load','set_line_catalog','remove_line')]
    return [dict(type='function',function=dict(name=name,description='Bounded engineering episode tool: '+name,parameters=dict(type='object',properties=specs[name][0],required=specs[name][1],additionalProperties=False))) for name in names]


def public_task(t):
    brief={'assemble':'Соберите заданную сеть: добавьте линии по разрешённым маршрутам и все нагрузки. Выберите кабели из каталога,выполните расчёт и подтвердите требования.','diagnose':'Найдите одну внесённую проблему. Используйте паспорта и измерения. Финальный ответ: cause и location. Менять сеть в диагностической задаче нельзя.','repair':'Найдите и исправьте одну внесённую проблему,сохранив источник,нагрузки и требования. Используйте только разрешённые маршруты и кабели,подтвердите результат новым расчётом.'}[t['kind']]
    return dict(id=t['id'],kind=t['kind'],split=t['split'],family_sha256=t['family_sha256'],brief=brief,requirements=requirements(t),budgets=dict(maximum_calls=32,maximum_simulations=4),tools=[name for name in TOOLS if t['kind']!='diagnose' or name not in ('add_line','add_load','set_line_catalog','remove_line')],tool_schemas=tool_schemas(t),diagnosis_classes=['impedance','ampacity','missing_connection'],diagnosis_location='equipment ID for impedance/ampacity;route ID for missing_connection',scope='Authored balanced400V passive PF only;one injected problem;not general causal diagnosis or qualified training corpus')


def reset(identifier,allow_private=False):
    t=task(identifier,allow_private);return dict(task_id=identifier,task_sha256=sha(public_task(t)),project=initial_project(t),revision=0,calls=0,simulations=0,last_result=None,last_result_revision=None,log=[],finished=False,terminal=None)


def observation(state,t):
    d=state['project'];return dict(task=public_task(t),revision=state['revision'],calls_remaining=max(0,32-state['calls']),simulations_remaining=max(0,4-state['simulations']),equipment={k:[v['id'] for v in d[k]] for k in ('nodes','lines','loads')},has_fresh_measurements=state['last_result_revision']==state['revision'] and state['last_result'] is not None,finished=state['finished'])


def _keys(args,required,optional=()):
    if not isinstance(args,dict) or not set(required)<=set(args) or set(args)-set(required)-set(optional):raise ValueError('Invalid tool arguments')
def _line_for_route(d,r):return next((l for l in d['lines'] if {l['from_node'],l['to_node']}=={r['from_node'],r['to_node']}),None)
def structure_errors(d,req):
    errors=[]
    if len(d['loads'])!=len(req['loads']) or any(not any(all(l[k]==v for k,v in owner.items()) for l in d['loads']) for owner in req['loads']):errors.append('required_loads')
    if len(d['lines'])!=len(req['routes']):errors.append('required_routes')
    for r in req['routes']:
        l=_line_for_route(d,r)
        if l is None:errors.append('missing_route:'+r['id']);continue
        if l['length_m']!=r['length_m'] or not any(all(l[k]==v for k,v in c.items()) for c in req['catalog'].values()):errors.append('catalog_passport:'+r['id'])
    return errors


def physical_errors(result,req):
    errors=[]
    if not result or result.get('converged') is not True:return ['incomplete_simulation']
    if any(not n['energized'] for n in result['nodes']):errors.append('deenergized_node')
    if any(p['voltage_pu']<req['minimum_voltage_pu'] for n in result['nodes'] if n['energized'] for p in n['phases'].values()):errors.append('minimum_voltage')
    if any(b['loading_pct'] is not None and b['loading_pct']>req['maximum_branch_loading_pct'] for b in result['branches']):errors.append('branch_loading')
    return errors


def finish(state,t,args):
    _keys(args,(),('cause','location','explanation') if t['kind']=='diagnose' else ('explanation',))
    if 'explanation' in args and (not isinstance(args['explanation'],str) or len(args['explanation'])>4000):raise ValueError('Explanation limit4000 characters')
    errors=[]
    if state['last_result'] is None or state['last_result_revision']!=state['revision']:errors.append('fresh_completed_simulation_required')
    req=requirements(t)
    if t['kind']=='diagnose':
        _keys(args,('cause','location'),('explanation',))
        expected_location='route1' if t['bug']=='missing_connection' else 'line1'
        if args['cause']!=t['bug'] or args['location']!=expected_location:errors.append('diagnosis_not_supported')
        if state['last_result'] and not physical_errors(state['last_result'],req):errors.append('no_observed_requirement_violation')
    else:
        errors+=structure_errors(state['project'],req)
        errors+=physical_errors(state['last_result'],req)
    state['finished']=True;state['terminal']=dict(success=not errors,reward=1. if not errors else 0.,violations=errors,answer=copy.deepcopy(args))
    return {k:v for k,v in state['terminal'].items() if k!='answer'}


def _public_measurements(result):
    return {k:copy.deepcopy(result[k]) for k in ('profile','status','converged','nodes','branches','loads','source','islands','warnings')}


def apply(state,name,args,allow_private=False):
    state=copy.deepcopy(state);t=task(state['task_id'],allow_private)
    if state['task_sha256']!=sha(public_task(t)):raise ValueError('Owner task revision differs')
    if name=='export':
        _keys(args,());return state,export_trace(state,t)
    if state['finished']:raise ValueError('Episode is already finished')
    if state['calls']>=32:state['finished']=True;state['terminal']=dict(success=False,reward=0.,violations=['call_budget'],answer={});return state,dict(ok=False,finished=True,reward=0.,error='call_budget',observation=observation(state,t))
    state['calls']+=1
    try:
        schema=next((s['function']['parameters'] for s in tool_schemas(t) if s['function']['name']==name),None)
        if schema is None:raise ValueError('Tool not allowed for this task')
        Draft202012Validator(schema).validate(args)
        d=state['project'];req=requirements(t);mutated=False
        if name=='inspect_project':_keys(args,());response=copy.deepcopy(d)
        elif name=='inspect_equipment':
            _keys(args,('equipment_id',));row=next((v for k in ('nodes','lines','loads') for v in d[k] if v['id']==args['equipment_id']),None)
            if row is None:raise ValueError('Unknown equipment')
            response=copy.deepcopy(row)
        elif name=='validate':
            _keys(args,());compile_draft(d);response=dict(valid=True,scope='Numerical compatibility only;not task success')
        elif name=='simulate':
            _keys(args,())
            if state['simulations']>=4:raise ValueError('simulation_budget')
            state['simulations']+=1
            state['last_result']=None;state['last_result_revision']=None
            result=solve(d);response=_public_measurements(result);state['last_result']=response;state['last_result_revision']=state['revision']
        elif name=='submit':response=finish(state,t,args)
        elif name in ('add_line','add_load','set_line_catalog','remove_line'):
            if t['kind']=='diagnose':raise ValueError('Diagnostic task is read-only')
            if name=='add_line':
                _keys(args,('route_id','equipment_id','catalog_id'))
                route=next((r for r in req['routes'] if r['id']==args['route_id']),None)
                catalog=req['catalog'].get(args['catalog_id'])
                if not route or not catalog or _line_for_route(d,route):raise ValueError('Route/catalog unavailable or connection already present')
                candidate=dict(id=args['equipment_id'],from_node=route['from_node'],to_node=route['to_node'],length_m=route['length_m'],**catalog)
                trial=copy.deepcopy(d);trial['lines'].append(candidate);Draft.model_validate(trial);d=trial
            elif name=='add_load':
                _keys(args,('load_id',));load=next((l for l in req['loads'] if l['id']==args['load_id']),None)
                if not load or any(l['id']==load['id'] for l in d['loads']):raise ValueError('Load unavailable or already present')
                trial=copy.deepcopy(d);trial['loads'].append(load);d=Draft.model_validate(trial).model_dump(mode='json',by_alias=True)
            elif name=='set_line_catalog':
                _keys(args,('equipment_id','catalog_id'));catalog=req['catalog'].get(args['catalog_id']);row=next((l for l in d['lines'] if l['id']==args['equipment_id']),None)
                if row is None or catalog is None:raise ValueError('Unknown line/catalog')
                row.update(catalog)
            else:
                _keys(args,('equipment_id',))
                if not any(l['id']==args['equipment_id'] for l in d['lines']):raise ValueError('Unknown line')
                d['lines']=[l for l in d['lines'] if l['id']!=args['equipment_id']]
            state['project']=d;mutated=True;response=dict(updated=True)
        else:raise ValueError('Unknown tool')
        if mutated:state['revision']+=1;state['last_result']=None;state['last_result_revision']=None
        response=dict(ok=True,result=response,observation=observation(state,t))
    except (ValueError,RuntimeError,ArithmeticError,SchemaError) as exc:response=dict(ok=False,error=str(exc)[:3000],observation=observation(state,t))
    state['log'].append(dict(name=name,arguments=copy.deepcopy(args),response=copy.deepcopy(response)))
    return state,response


def export_trace(state,t):
    messages=[dict(role='system',content='Solve this bounded engineering task using only the provided tools. Never change owner requirements. A solver failure is not success.'),dict(role='user',content=json.dumps(public_task(t),ensure_ascii=False))]
    for i,entry in enumerate(state['log']):
        if entry['name']=='submit':
            if entry['response'].get('ok'):messages.append(dict(role='assistant',content=json.dumps(entry['arguments'],ensure_ascii=False)))
            continue
        call='call_'+str(i);messages.append(dict(role='assistant',content=None,tool_calls=[dict(id=call,type='function',function=dict(name=entry['name'],arguments=json.dumps(entry['arguments'],ensure_ascii=False)))]));messages.append(dict(role='tool',tool_call_id=call,content=json.dumps(entry['response'],ensure_ascii=False)))
    return dict(schema='power-grid-env.engineering-episode/v1',task_id=t['id'],split=t['split'],family_sha256=t['family_sha256'],task_sha256=state['task_sha256'],tools=tool_schemas(t),messages=messages,terminal={k:v for k,v in (state['terminal'] or {}).items() if k!='answer'},eligible_for_gold_sft=False,scope='Public tool transcript;authored task checker,not independent physics qualification or evidence of trained-agent capability')


def teacher(identifier,allow_private=False):
    s=reset(identifier,allow_private);t=task(identifier,allow_private)
    def call(name,args):
        nonlocal s
        s,r=apply(s,name,args,allow_private)
        if not r.get('ok'):raise ValueError(r.get('error'))
        return r
    call('inspect_project',{})
    if t['kind']=='assemble':
        for i,r in enumerate(requirements(t)['routes'],1):call('add_line',dict(route_id=r['id'],equipment_id='line'+str(i),catalog_id='copper-1'))
        for load in requirements(t)['loads']:call('add_load',dict(load_id=load['id']))
    elif t['kind']=='repair':
        call('simulate',{})
        if t['bug']=='missing_connection':call('add_line',dict(route_id='route1',equipment_id='line1',catalog_id='copper-1'))
        else:call('set_line_catalog',dict(equipment_id='line1',catalog_id='copper-1'))
    call('validate',{});call('simulate',{})
    answer=dict(cause=t['bug'],location='route1' if t['bug']=='missing_connection' else 'line1') if t['kind']=='diagnose' else {}
    call('submit',answer)
    return s


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline-out');args=p.parse_args()
    if args.baseline_out:
        root=Path(args.baseline_out);root.mkdir(parents=True,exist_ok=False);rows=[]
        for t in registry():
            s=teacher(t['id'],True);rows.append(dict(task_id=t['id'],split=t['split'],family_sha256=t['family_sha256'],**{k:v for k,v in s['terminal'].items() if k!='answer'},calls=s['calls'],simulations=s['simulations']))
            if t['split']=='train':(root/(t['id']+'.episode.json')).write_text(json.dumps(export_trace(s,t),ensure_ascii=False,indent=2))
        summary=dict(schema='power-grid-env.engineering-agent-baseline/v1',passed=all(r['success'] for r in rows),tasks=len(rows),families=len(FAMILIES),rows=rows,core_sha256=package_fingerprint(),episode_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),bridge_code_sha256=hashlib.sha256(Path(__file__).with_name('grid_editor.py').read_bytes()+Path(__file__).with_name('editor_steady.py').read_bytes()).hexdigest(),task_inventory_sha256=sha([public_task(t) for t in registry()]),scope='Privileged deterministic teacher verifies task plumbing;not a learned baseline,independent task oracle or qualified SFT corpus')
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));seal(root);print(json.dumps({k:v for k,v in summary.items() if k!='rows'}));return
    try:
        req=json.loads(sys.stdin.buffer.read(2000001));action=req['action']
        if action=='tasks':out=dict(tasks=[public_task(t) for t in registry() if t['split']=='train'])
        elif action=='reset':
            s=reset(req['task_id']);out=dict(private_state=s,public=observation(s,task(s['task_id'])))
        elif action=='observe':
            s=req['state'];t=task(s['task_id'])
            if s['task_sha256']!=sha(public_task(t)):raise ValueError('Owner task revision differs')
            out=dict(private_state=s,public=observation(s,t),measurements=s['last_result'] if s['last_result_revision']==s['revision'] else None,journal=s['log'],terminal={k:v for k,v in (s['terminal'] or {}).items() if k!='answer'})
        elif action=='tool':
            s,r=apply(req['state'],req['name'],req['arguments']);out=dict(private_state=s,public=r)
        else:raise ValueError('Unknown worker action')
        print(json.dumps(dict(ok=True,**out),ensure_ascii=False,allow_nan=False))
    except Exception as exc:print(json.dumps(dict(ok=False,error=str(exc)[:3000]),ensure_ascii=False));sys.exit(1)
if __name__=='__main__':main()

"""Procedural, topology-grouped owner scenarios and bounded public tool engine."""
import copy
import random
from jsonschema import Draft202012Validator,ValidationError
from benchmarks import engineering_agent as legacy
from benchmarks.grid_editor import Draft,compile_draft,solve


class ScenarioEngine:
    def __init__(self,scenario):
        self.scenario=copy.deepcopy(scenario)
        self.requirements=copy.deepcopy(scenario['requirements'])
        self.state=dict(task_id=scenario['id'],project=Draft.model_validate(scenario['project']).model_dump(mode='json',by_alias=True),
            revision=0,calls=0,simulations=0,last_result=None,last_result_revision=None,finished=False,terminal=None)

    def schemas(self):
        specs=legacy.tool_schemas(dict(kind=self.scenario['kind'],parents=self.scenario['parents']))
        for spec in specs:
            name=spec['function']['name'];properties=spec['function']['parameters']['properties']
            if name=='add_line':properties['route_id']['enum']=[r['id'] for r in self.requirements['routes']]
            if name=='add_load':properties['load_id']['enum']=[l['id'] for l in self.requirements['loads']]
            if 'catalog_id' in properties:properties['catalog_id']['enum']=list(self.requirements['catalog'])
        return [s for s in specs if s['function']['name']!='export']

    def public_task(self):
        return dict(id=self.scenario['id'],kind=self.scenario['kind'],split=self.scenario['split'],family_sha256=self.scenario['family_sha256'],
            brief='Build, inspect/diagnose, or repair this bounded passive grid as specified by kind. Preserve all loads, routes and source. Validate and submit with fresh simulation.',
            requirements=copy.deepcopy(self.requirements),tools=[s['function']['name'] for s in self.schemas()],tool_schemas=self.schemas(),
            budgets=dict(maximum_calls=24,maximum_simulations=4),diagnosis_classes=list(legacy.BUGS),
            scope='Synthetic balanced400V constant-Z radial profile;no general diagnosis,protection or normative approval')

    def observation(self):
        s=self.state
        return dict(task=self.public_task(),revision=s['revision'],calls_remaining=max(0,24-s['calls']),simulations_remaining=max(0,4-s['simulations']),
            has_fresh_measurements=s['last_result'] is not None and s['last_result_revision']==s['revision'],finished=s['finished'])

    def apply(self,name,args):
        s=self.state
        if s['finished']:raise ValueError('terminal episode')
        if s['calls']>=24:
            s['finished']=True;s['terminal']=dict(success=False,reward=0.,violations=['call_budget'])
            return dict(ok=False,error='call_budget',observation=self.observation())
        s['calls']+=1
        try:
            schema=next((x['function']['parameters'] for x in self.schemas() if x['function']['name']==name),None)
            if schema is None:raise ValueError('tool not allowed')
            Draft202012Validator(schema).validate(args)
            d=copy.deepcopy(s['project']);req=self.requirements;mutated=False
            if name=='inspect_project':result=d
            elif name=='inspect_equipment':
                result=next((x for key in ('nodes','lines','loads') for x in d[key] if x['id']==args['equipment_id']),None)
                if result is None:raise ValueError('unknown equipment')
            elif name=='validate':compile_draft(d);result=dict(valid=True)
            elif name=='simulate':
                if s['simulations']>=4:raise ValueError('simulation_budget')
                s['simulations']+=1;s['last_result']=None;s['last_result_revision']=None
                result=legacy._public_measurements(solve(d))
                s['last_result']=copy.deepcopy(result);s['last_result_revision']=s['revision']
            elif name=='submit':
                errors=[]
                if s['last_result'] is None or s['last_result'].get('converged') is not True or s['last_result_revision']!=s['revision']:errors.append('fresh_completed_simulation_required')
                if self.scenario['kind']=='diagnose':
                    if args['cause']!=self.scenario['truth']['cause'] or args['location']!=self.scenario['truth']['location']:errors.append('diagnosis_not_supported')
                    if s['last_result'] and not legacy.physical_errors(s['last_result'],req):errors.append('no_observed_requirement_violation')
                else:errors+=legacy.structure_errors(d,req)+legacy.physical_errors(s['last_result'],req)
                result=dict(success=not errors,reward=float(not errors),violations=errors)
                s['terminal']=copy.deepcopy(result);s['finished']=True
            elif name=='add_line':
                route=next((r for r in req['routes'] if r['id']==args['route_id']),None)
                if route is None or legacy._line_for_route(d,route):raise ValueError('route already used or unavailable')
                d['lines'].append(dict(id=args['equipment_id'],from_node=route['from_node'],to_node=route['to_node'],length_m=route['length_m'],**req['catalog'][args['catalog_id']]))
                mutated=True;result=dict(updated=True)
            elif name=='add_load':
                load=next(l for l in req['loads'] if l['id']==args['load_id'])
                if any(l['id']==load['id'] for l in d['loads']):raise ValueError('load already present')
                d['loads'].append(copy.deepcopy(load));mutated=True;result=dict(updated=True)
            elif name=='set_line_catalog':
                line=next((l for l in d['lines'] if l['id']==args['equipment_id']),None)
                if line is None:raise ValueError('unknown line')
                line.update(req['catalog'][args['catalog_id']]);mutated=True;result=dict(updated=True)
            elif name=='remove_line':
                if not any(l['id']==args['equipment_id'] for l in d['lines']):raise ValueError('unknown line')
                d['lines']=[l for l in d['lines'] if l['id']!=args['equipment_id']];mutated=True;result=dict(updated=True)
            else:raise ValueError('unknown tool')
            if mutated:
                s['project']=Draft.model_validate(d).model_dump(mode='json',by_alias=True)
                s['revision']+=1;s['last_result']=None;s['last_result_revision']=None
            return dict(ok=True,result=result,observation=self.observation())
        except (ValueError,RuntimeError,ArithmeticError,ValidationError) as exc:
            return dict(ok=False,error=str(exc)[:3000],observation=self.observation())


def family_pool(seed=420,counts=(8,3,3)):
    rng=random.Random(seed);excluded={legacy.family_key(v[1]) for v in legacy.FAMILIES.values()};families={}
    for _ in range(10000):
        n=rng.randrange(4,8);parents=[-1]+[rng.randrange(i) for i in range(1,n)]
        key=legacy.family_key(parents)
        if key not in excluded:families.setdefault(key,parents)
        if len(families)==sum(counts):break
    if len(families)!=sum(counts):raise ValueError('insufficient distinct rooted families')
    keys=sorted(families);rng.shuffle(keys);result=[];cursor=0
    for split,count in zip(('train','dev','test'),counts):
        for key in keys[cursor:cursor+count]:result.append(dict(split=split,family_sha256=key,parents=families[key]))
        cursor+=count
    return result


def generate_scenarios(seed=420,variants=2,counts=(8,3,3)):
    if type(variants) is not int or not 1<=variants<=8:raise ValueError('variants1..8')
    output=[]
    for family in family_pool(seed,counts):
        for variant in range(variants):
            base_id=legacy.sha(dict(family=family['family_sha256'],variant=variant,seed=seed,version='procedural-v1'))
            rng=random.Random(int(base_id[:16],16));parents=family['parents'];n=len(parents)
            nodes=['bus_'+str(rng.randrange(10**8,10**9)) for _ in range(n)]
            catalog={'copper-1':dict(r_ohm_km=rng.uniform(.2,.4),x_ohm_km=rng.uniform(.05,.1),rating_a=rng.uniform(150,220)),
                     'copper-2':dict(r_ohm_km=rng.uniform(.08,.16),x_ohm_km=rng.uniform(.04,.08),rating_a=rng.uniform(250,400))}
            routes=[dict(id='route_'+str(rng.randrange(10**8,10**9)),from_node=nodes[p],to_node=nodes[i],length_m=rng.uniform(80,120)) for i,p in enumerate(parents) if i]
            loads=[dict(id='load_'+str(rng.randrange(10**8,10**9)),node_id=node,p_kw=rng.uniform(4,6),q_kvar=rng.uniform(.7,1.6)) for node in nodes[1:]]
            req=dict(source_node=nodes[0],nominal_ll_v=400.,routes=routes,loads=loads,catalog=catalog,minimum_voltage_pu=.95,maximum_branch_loading_pct=100.,all_loads_energized=True)
            base=legacy.initial_project(dict(parents=parents,kind='assemble',bug=None))
            base.update(source_node=nodes[0],source_r_ohm=rng.uniform(.001,.004),source_x_ohm=rng.uniform(.005,.015),
                nodes=[dict(id=node,nominal_ll_v=400.,x=rng.uniform(20,1900),y=rng.uniform(20,1300)) for node in nodes],loads=copy.deepcopy(loads),
                lines=[dict(id='line_'+str(rng.randrange(10**8,10**9)),from_node=r['from_node'],to_node=r['to_node'],length_m=r['length_m'],**catalog['copper-1']) for r in routes],
                fault=dict(node_id=nodes[0],r_ohm=.001,x_ohm=0.))
            index=rng.randrange(len(routes))
            routes[index]['length_m']=120.;base['lines'][index]['length_m']=120.
            for kind,cause in [('assemble',None)]+[(kind,cause) for kind in ('diagnose','repair') for cause in legacy.BUGS]:
                project=copy.deepcopy(base);truth=None
                if kind=='assemble':project['lines']=[];project['loads']=[]
                else:
                    line=project['lines'][index];truth=dict(cause=cause,location=routes[index]['id'] if cause=='missing_connection' else line['id'])
                    if cause=='impedance':line['r_ohm_km']=19.
                    elif cause=='ampacity':line['rating_a']=1.
                    else:project['lines'].pop(index)
                rng.shuffle(project['nodes']);rng.shuffle(project['lines']);rng.shuffle(project['loads'])
                taskid=legacy.sha(dict(base=base_id,kind=kind,cause=cause,version='scenario-v1'))
                output.append(dict(id='case-'+taskid[:24],base_project_id=base_id,split=family['split'],family_sha256=family['family_sha256'],
                    parents=copy.deepcopy(parents),kind=kind,requirements=copy.deepcopy(req),project=project,truth=truth,
                    healthy_project=copy.deepcopy(base),parameter_variant=variant,parameter_provenance='owner_synthetic_bounded_uniform_not_OEM'))
    return output

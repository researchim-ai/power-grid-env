"""Public solver-assisted engineering workflow, separate from exact SFT/v2.

Both LLMs and controls receive the same tools, computed facts and budgets.
Hypothetical studies do not mutate the real project or certify a stale result.
"""
import copy
import json

from jsonschema import Draft202012Validator
from benchmarks.engineering_constraint_tasks import assess
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_sft_contract_v2 import ExactEpisode,structure_assessment
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.grid_editor import solve


MAX_ACTIONS=10
MAX_PREVIEWS=4


def assessment(episode):
    card,_=episode.observe()
    if not card['has_fresh_measurements']:return None
    status=assess(card);structure=structure_assessment(card)
    nodes=card['study']['nodes'];branches=card['study']['branches']
    return dict(converged=card['study']['converged'],measurement_feasible=status['measurement_feasible'],
                structure_matches=structure['structure_matches'],
                minimum_voltage_pu=min(n['voltage_pu_min'] for n in nodes if n['energized']) if any(n['energized'] for n in nodes) else None,
                maximum_branch_loading_pct=max((b['loading_pct'] for b in branches if b['loading_pct'] is not None),default=None),
                overloaded_branches=[dict(id=b['id'],loading_pct=b['loading_pct']) for b in branches if b['loading_pct'] is not None and b['loading_pct']>card['goal']['maximum_branch_loading_pct']],
                undervoltage_nodes=[n['id'] for n in nodes if n['energized'] and n['voltage_pu_min']<card['goal']['minimum_voltage_pu']],
                deenergized_nodes=[n['id'] for n in nodes if not n['energized']])


class AssistedEpisode:
    def __init__(self,case):
        self.episode=ExactEpisode(case)
        self.preview_count=0;self.preview_results={};self.calls=0;self.physics_checks=[]

    def public(self):
        card,_=self.episode.observe()
        tools=copy.deepcopy(card['tools'])
        for tool in tools:
            name=tool['function']['name']
            tool['function']['description']=card['tool_effects'][name]
            # Match the public-handle resolver exactly; e.g. repair submit does
            # not accept a narrative explanation in this interface.
            fields={'inspect_project':set(),'inspect_equipment':{'equipment_id'},'validate':set(),'simulate':set(),
                    'submit':{'cause','location'},'set_line_catalog':{'equipment_id','catalog_id'},
                    'remove_line':{'equipment_id'},'add_line':{'route_id','catalog_id'},'add_load':{'load_id'}}[name]
            params=tool['function']['parameters']
            params['properties']={k:v for k,v in params['properties'].items() if k in fields}
            params['required']=[k for k in params.get('required',[]) if k in fields]
        tools.append(dict(type='function',function=dict(name='preview_line_catalog',
            description='Run a hypothetical physical study with one installed line replaced by a catalogue passport. Returns measurements and public constraint checks. Does not change the actual project, revision, mutation budget or actual-study freshness. Costs one action and one preview study.',
            parameters=dict(type='object',additionalProperties=False,required=['equipment_id','catalog_id'],
                properties=dict(equipment_id=dict(type='string',enum=[x['id'] for x in card['scene']['lines']]),
                                catalog_id=dict(type='string',enum=[x['id'] for x in card['scene']['catalog']]))))))
        return dict(profile='solver-assisted-engineering/v1',goal=card['goal'],revision=card['revision'],
                    validated=card['validated'],has_fresh_measurements=card['has_fresh_measurements'],
                    calls_remaining=MAX_ACTIONS-self.calls,actual_simulations_remaining=card['simulations_remaining'],
                    preview_simulations_remaining=MAX_PREVIEWS-self.preview_count,
                    catalog_edits_used=card['catalog_edits_used'],changed_routes=card['changed_routes'],
                    scene=card['scene'],units=card['units'],current_result=assessment(self.episode),
                    previews=[v for _,v in sorted(self.preview_results.items()) if v['base_revision']==card['revision']],tools=tools)

    def apply(self,action):
        if self.episode.engine.state['finished'] or self.calls>=MAX_ACTIONS:raise ValueError('terminal/action limit')
        card=self.public();self.calls+=1
        try:
            if not isinstance(action,dict) or set(action)!={'name','arguments'} or not isinstance(action['arguments'],dict):
                raise ValueError('one name/arguments action required')
            name=action['name']
            spec=next((x['function'] for x in card['tools'] if x['function']['name']==name),None)
            if spec is None or not Draft202012Validator(spec['parameters']).is_valid(action['arguments']):
                raise ValueError('unknown operation or invalid arguments')
            if name=='preview_line_catalog':
                if self.preview_count>=MAX_PREVIEWS:raise ValueError('preview study budget')
                # Count a call in the real owner, but mutations/studies happen on
                # a copy. No healthy_project/truth/reference action is consulted.
                self.episode.engine.state['calls']+=1
                shadow=copy.deepcopy(self.episode)
                reply,_=shadow.apply(dict(name='set_line_catalog',arguments=action['arguments']))
                if reply.get('ok') is not True:raise ValueError('preview equipment rejected')
                self.preview_count+=1
                project=shadow.engine.state['project'];result=solve(project)
                proof=compare_reference(project,result);self.physics_checks.append(dict(kind='preview',**proof))
                shadow.engine.state['last_result']=result
                shadow.engine.state['last_result_revision']=shadow.engine.state['revision']
                value=dict(base_revision=card['revision'],**action['arguments'],assessment=assessment(shadow),
                           calculation='hypothetical_constant_z_power_flow')
                self.preview_results[sha([card['revision'],action['arguments']])]=value
                return dict(ok=True,result=value)
            reply,_=self.episode.apply(action)
            if name=='simulate' and reply.get('ok') is True:
                proof=compare_reference(self.episode.engine.state['project'],self.episode.engine.state['last_result'])
                self.physics_checks.append(dict(kind='actual',**proof))
            # The next public packet contains the compact current assessment;
            # do not duplicate the full phase-by-phase simulation in the prompt.
            return dict(ok=reply.get('ok') is True,error=reply.get('error'),
                        result=reply.get('result') if name!='simulate' else assessment(self.episode))
        except (ValueError,TypeError,RuntimeError,ArithmeticError) as exc:
            return dict(ok=False,error=type(exc).__name__+':'+str(exc)[:500])


def request(public,last_reply=None):
    value=copy.deepcopy(public)
    if last_reply is not None:value['last_tool_reply']=copy.deepcopy(last_reply)
    instruction=('ENGINEERING-WORKFLOW/V1. Satisfy every public network goal while preserving all required loads/routes and equipment budgets. '
        'Use tools for numerical studies. A preview is hypothetical; only simulate refreshes the actual network study. '
        'validate checks compilation only. Mutations invalidate actual measurements. submit is terminal and requires a fresh feasible actual study. '
        'Choose one useful allowed action. Return only JSON {"name":"tool name","arguments":{...}}, no explanation or Markdown. '
        'No hidden answer or recommended equipment is supplied. Every call, including errors, spends the action budget.')
    return dict(messages=[dict(role='system',content=instruction),dict(role='user',content=json.dumps(value,separators=(',',':')))],
                maximum_completion_tokens=128)


def progress_signature(public):
    return sha({k:public[k] for k in ('revision','validated','has_fresh_measurements','current_result','previews')})


def run(case,policy):
    owner=AssistedEpisode(case);events=[];previous=None;last_action=None;stagnant=0;stop_reason=None
    while not owner.episode.engine.state['finished'] and owner.calls<MAX_ACTIONS:
        public=owner.public();query=request(public,previous);raw=None;action=None;policy_error=None
        try:
            raw=policy(copy.deepcopy(query));action=bounded_json(raw)
        except Exception as exc:
            policy_error=type(exc).__name__+':'+str(exc)[:500]
            action=dict(name='__invalid_action__',arguments={})
            raw=raw if isinstance(raw,str) else None
        reply=owner.apply(action);after=owner.public()
        events.append(dict(public=public,query=query,completion=raw,action=action,reply=reply,policy_error=policy_error))
        signature=sha(action)
        if progress_signature(public)==progress_signature(after) and signature==last_action:stagnant+=1
        else:stagnant=0
        last_action=signature;previous=reply
        if stagnant>=2 and not owner.episode.engine.state['finished']:
            stop_reason='repeated_nonprogress';break
    if not owner.episode.engine.state['finished']:
        owner.episode.engine.state['finished']=True
        owner.episode.engine.state['terminal']=dict(success=False,reward=0.,violations=[stop_reason or 'workflow_action_budget'])
    return dict(profile='solver-assisted-engineering/v1',task_id=case['id'],split=case['split'],
                family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],events=events,
                terminal=owner.episode.engine.state['terminal'],success=owner.episode.engine.state['terminal']['success'] is True,
                calls=owner.calls,preview_studies=owner.preview_count,physics_checks=owner.physics_checks,
                scope='Ten-action solver-assisted synthetic balanced constant-Z workflow; independent reference audits, no teacher fallback.')


def control(query):
    """Explicit scripted controller using only the same public tools/results."""
    p=json.loads(query['messages'][1]['content'])
    if not p['has_fresh_measurements']:action=dict(name='simulate',arguments={})
    elif p['current_result']['measurement_feasible'] and p['current_result']['structure_matches']:
        action=dict(name='submit',arguments={})
    else:
        overloaded=p['current_result']['overloaded_branches']
        if not overloaded:action=dict(name='submit',arguments={})
        else:
            line=max(overloaded,key=lambda x:x['loading_pct'])['id']
            studied={r['catalog_id']:r for r in p['previews'] if r['equipment_id']==line}
            good=next((r for r in studied.values() if r['assessment']['measurement_feasible'] and r['assessment']['structure_matches']),None)
            if good:action=dict(name='set_line_catalog',arguments=dict(equipment_id=line,catalog_id=good['catalog_id']))
            else:
                actual=next(r for r in p['scene']['lines'] if r['id']==line)
                candidates=[c for c in p['scene']['catalog'] if c['id'] not in studied and not all(actual[k]==v for k,v in c.items() if k!='id')]
                action=dict(name='preview_line_catalog',arguments=dict(equipment_id=line,catalog_id=candidates[0]['id'])) if candidates else dict(name='submit',arguments={})
    return json.dumps(action)

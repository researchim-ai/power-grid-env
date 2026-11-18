"""Two-stage public agent API:operation,then arguments selected from observations.

Argument prompts contain no teacher directive or preselected equipment/catalog.
Full episodes keep the v2 route-change budget and use the physical terminal.
"""
import copy,json
from jsonschema import Draft202012Validator
from benchmarks.engineering_operation_probe import messages as operation_messages
from benchmarks.engineering_rollout import strict_json
from benchmarks.engineering_replacement_challenges import ReplacementEpisode


def operation_request(card,feedback=None):
    public=copy.deepcopy(card)
    if feedback is not None:public['previous_feedback']=copy.deepcopy(feedback)
    return dict(stage='operation',messages=operation_messages(public),maximum_completion_tokens=128)


def argument_request(card,operation,feedback=None):
    spec=next((t for t in card['tools'] if t['function']['name']==operation),None)
    if spec is None:raise ValueError('operation is not publicly allowed')
    public=copy.deepcopy(card);public.pop('tools')
    public['given_operation']=operation;public['argument_schema']=spec['function']['parameters']
    if feedback is not None:public['previous_feedback']=copy.deepcopy(feedback)
    instruction=('CONDITIONAL-ARGUMENTS/V1. The operation is given. Choose its arguments from '
                 'the public goal,current equipment and fresh measurements. Return only the JSON '
                 'arguments object,using public aliases and the schema. Do not change requirements '
                 'or invent equipment. No operation wrapper or Markdown.')
    return dict(stage='arguments',messages=[dict(role='system',content=instruction),
        dict(role='user',content=json.dumps(public,separators=(',',':')))],maximum_completion_tokens=128)


def bounded_json(raw):
    if not isinstance(raw,str) or len(raw.encode())>8192:raise ValueError('bounded JSON completion required')
    result=strict_json(raw)
    if not isinstance(result,dict):raise ValueError('one JSON object required')
    return result


def decide(card,policy,feedback=None):
    requests=[];completions=[]
    try:
        q=operation_request(card,feedback);requests.append(copy.deepcopy(q));raw=policy(copy.deepcopy(q));completions.append(raw if isinstance(raw,str) else None)
        value=bounded_json(raw)
        if set(value)!={'operation'} or not isinstance(value['operation'],str):raise ValueError('one operation name required')
        operation=value['operation'];spec=next((t for t in card['tools'] if t['function']['name']==operation),None)
        if spec is None:raise ValueError('unknown public operation')
        schema=spec['function']['parameters']
        # Omit optional arguments when {} is schema-valid. This profile learns
        # required arguments;empty-argument serialization is adapter bookkeeping.
        if not schema.get('required') and Draft202012Validator(schema).is_valid({}):args={}
        else:
            q=argument_request(card,operation,feedback);requests.append(copy.deepcopy(q));raw=policy(copy.deepcopy(q));completions.append(raw if isinstance(raw,str) else None)
            args=bounded_json(raw)
        return dict(action=dict(name=operation,arguments=args),requests=requests,completions=completions,error=None)
    except Exception as exc:
        return dict(action=None,requests=requests,completions=completions,error=type(exc).__name__+':'+str(exc)[:500])


def public_feedback(reply,action):
    return dict(ok=reply.get('ok') is True,error=reply.get('error'),operation=action['name'] if action else None)


def run(case,policy,episode_factory=ReplacementEpisode):
    e=episode_factory(case);events=[];feedback=None
    while not e.engine.state['finished'] and e.engine.state['calls']<24:
        card,_=e.observe();decision=decide(card,policy,feedback)
        if decision['action'] is None:reply=e.engine.apply('__invalid_action__',{})
        else:reply,_=e.apply(decision['action'])
        events.append(dict(card=card,previous_feedback=feedback,decision=decision,response=reply))
        feedback=public_feedback(reply,decision['action'])
    if not e.engine.state['finished']:e.engine.apply('__invalid_action__',{})
    return dict(profile='staged-operation-args/v1',task_id=case['id'],base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],split=case['split'],
        calls=e.engine.state['calls'],candidate_completions=sum(len(x['decision']['completions']) for x in events),events=events,
        protocol_errors=sum(x['decision']['error'] is not None for x in events),terminal=copy.deepcopy(e.engine.state['terminal']),
        success=e.engine.state['terminal']['success'] is True,
        scope='Full public staged workflow,requests up to128 tokens per stage;optional args omitted when empty object is valid;byte cap enforced,model adapter must enforce generation token cap;no teacher fallback or OS sandbox')


def catalog_control(q):
    """PUBLIC algorithmic reference,not a trained model."""
    c=json.loads(q['messages'][-1]['content'])
    if q['stage']=='operation':
        if not c['validated']:op='validate'
        elif not c['has_fresh_measurements']:op='simulate'
        elif any(r['loading_pct'] is not None and r['loading_pct']>100 for r in c['study']['branches']):op='set_line_catalog'
        else:op='submit'
        return json.dumps(dict(operation=op))
    if c['given_operation']!='set_line_catalog':raise ValueError('reference does not cover this parameterized operation')
    line=max(c['study']['branches'],key=lambda r:r['loading_pct']);catalog=max(c['scene']['catalog'],key=lambda r:float(r['rating_a']))
    return json.dumps(dict(equipment_id=line['id'],catalog_id=catalog['id']))

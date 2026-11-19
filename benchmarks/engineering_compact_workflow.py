"""Compact tool orchestration with explicit optional-empty argument semantics.

Different declared observation/action profile from strict full-card workflows.
Only catalogue changes are possible; complete initial structure is a precondition.
"""
import copy
import json
from jsonschema import Draft202012Validator

from benchmarks.engineering_assisted_workflow import AssistedEpisode,assessment,progress_signature,MAX_ACTIONS
from benchmarks.engineering_sft_contract_v2 import structure_assessment
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_sft_release_v2 import sha


DESCRIPTIONS={
    'simulate':'Solve the actual network; refresh current_result. Costs an actual study.',
    'validate':'Check compilation only; does not assess physical limits or refresh measurements.',
    'preview_line_catalog':'Solve a hypothetical replacement. Actual network/freshness unchanged. Costs a preview study.',
    'set_line_catalog':'Apply a passport to the actual line. Uses one edit and invalidates actual measurements.',
    'submit':'Finish. Requires a fresh actual study and all physical/structural/equipment-budget constraints.'}


class CompactEpisode(AssistedEpisode):
    def __init__(self,case):
        super().__init__(case)
        if not structure_assessment(self.episode.observe()[0])['structure_matches']:
            raise ValueError('compact profile requires complete initial loads/routes/passports')
    def public(self):
        full=super().public()
        full['profile']='compact-solver-orchestration/v1'
        full['network']=dict(nodes=len(full['scene']['nodes']),loads=len(full['scene']['loads']),
                             nominal_ll_v=full['scene']['nominal_ll_v'],
                             structure='Loads/routes are fixed and initially match requirements; only line passports may change.')
        full['scene']={k:full['scene'][k] for k in ('lines','catalog')}
        full.pop('units')
        full['tools']=[t for t in full['tools'] if t['function']['name'] in DESCRIPTIONS]
        for t in full['tools']:t['function']['description']=DESCRIPTIONS[t['function']['name']]
        return full


def request(public,last_reply=None):
    value=copy.deepcopy(public)
    if last_reply is not None:value['last_tool_status']={k:last_reply.get(k) for k in ('ok','error')}
    instruction=('GRID-ENGINEER-COMPACT/V1. Meet every public network goal within budgets. Use physical study tools. '
        'Preview results describe hypothetical replacements; only simulate refreshes the actual network. '
        'After a change, actual measurements are stale. submit is terminal. Choose one useful tool call. '
        'Return JSON with name and arguments. You may OMIT arguments only when that tool accepts an empty object; '
        'the API then uses {}. Required equipment/catalog arguments must be selected by you. No Markdown/explanation.')
    return dict(messages=[dict(role='system',content=instruction),dict(role='user',content=json.dumps(value,separators=(',',':')))],
                maximum_completion_tokens=128)


def decode(raw,public):
    value=bounded_json(raw)
    if set(value) not in ({'name'},{'name','arguments'}) or not isinstance(value['name'],str):
        raise ValueError('one known tool name and optional arguments object required')
    spec=next((t['function']['parameters'] for t in public['tools'] if t['function']['name']==value['name']),None)
    if spec is None:raise ValueError('tool outside this profile')
    default='arguments' not in value
    args=value.get('arguments',{})
    if not Draft202012Validator(spec).is_valid(args):raise ValueError('required arguments missing or invalid')
    return dict(name=value['name'],arguments=args),default


def run(case,policy):
    owner=CompactEpisode(case);events=[];previous=None;last=None;stagnant=0;stop=None
    while not owner.episode.engine.state['finished'] and owner.calls<MAX_ACTIONS:
        public=owner.public();query=request(public,previous)
        raw=policy(copy.deepcopy(query)) # Backend failures are not model-quality scores.
        error=None;default=False
        try:action,default=decode(raw,public)
        except (ValueError,TypeError) as exc:
            error=type(exc).__name__+':'+str(exc)[:500]
            action=dict(name='__invalid_action__',arguments={})
        reply=owner.apply(action);after=owner.public()
        events.append(dict(public=public,query=query,completion=raw,action=action,
                           default_empty_arguments_used=default,decode_error=error,reply=reply))
        signature=sha(action)
        if progress_signature(public)==progress_signature(after) and signature==last:stagnant+=1
        else:stagnant=0
        last=signature;previous=reply
        if stagnant>=2 and not owner.episode.engine.state['finished']:stop='repeated_nonprogress';break
    if not owner.episode.engine.state['finished']:
        owner.episode.engine.state['finished']=True
        owner.episode.engine.state['terminal']=dict(success=False,reward=0.,violations=[stop or 'workflow_action_budget'])
    return dict(profile='compact-solver-orchestration/v1',task_id=case['id'],split=case['split'],family_sha256=case['family_sha256'],
                base_project_id=case['base_project_id'],events=events,terminal=owner.episode.engine.state['terminal'],
                success=owner.episode.engine.state['terminal']['success'] is True,calls=owner.calls,
                preview_studies=owner.preview_count,physics_checks=owner.physics_checks,
                scope='Compact immutable-structure catalogue workflow; optional empty arguments are declared API defaults, required choices remain model-owned.')

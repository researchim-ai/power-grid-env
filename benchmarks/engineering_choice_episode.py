"""Owner-run action-choice episodes; candidate receives public data only.

No teacher correction, feasibility filtering or format repair is applied to
candidate output. Callable separation is a data-flow boundary, not an OS sandbox.
"""
import copy
import json
from benchmarks.engineering_action_menu import candidates
from benchmarks.engineering_skill_tasks import shuffled_menu,card_messages
from benchmarks.engineering_card_curriculum import CardEpisode
from benchmarks.engineering_catalog_challenges import CatalogEpisode
from benchmarks.engineering_rollout import strict_json


def request(card,seed):
    menu=shuffled_menu(card,seed)
    # card_messages also constructs a training label. A neutral PUBLIC menu item
    # supplies that parameter here; the label is discarded and never transmitted.
    messages,_=card_messages(dict(skill='choice',card=card,action=menu[0]['action']),seed)
    return dict(profile='action-choice/v2',card=copy.deepcopy(card),choices=menu,messages=messages)


def parse(raw,menu):
    if not isinstance(raw,str) or len(raw.encode())>8192:raise ValueError('bounded JSON choice string required')
    value=strict_json(raw)
    if not isinstance(value,dict) or set(value)!={'choice'} or not isinstance(value['choice'],str):raise ValueError('one choice field required')
    action=next((m['action'] for m in menu if m['choice']==value['choice']),None)
    if action is None:raise ValueError('unknown public choice')
    return copy.deepcopy(action)


def run(case,policy,seed=420,catalog=False,episode_factory=None):
    if catalog and episode_factory is not None:raise ValueError('one owner episode profile required')
    e=episode_factory(case) if episode_factory is not None else CatalogEpisode(case) if catalog else CardEpisode(case)
    rows=[];errors=0
    while not e.engine.state['finished'] and e.engine.state['calls']<24:
        card,_=e.observe();q=request(card,seed+len(rows));raw=None;action=None;error=None
        try:
            raw=policy(copy.deepcopy(q))
        except Exception as exc:
            error='candidate_error:'+type(exc).__name__
        if error is None:
            try:action=parse(raw,q['choices'])
            except (ValueError,TypeError) as exc:error='invalid_choice:'+str(exc)
        if error:
            errors+=1
            reply=e.engine.apply('__invalid_action__',{})
        else:
            reply,_=e.apply(action)
        rows.append(dict(card=card,menu=q['choices'],completion=raw if isinstance(raw,str) else None,
                         action=action,error=error,response=reply))
    if not e.engine.state['finished']:
        # The engine publishes the call-budget failure without an extra decision.
        e.engine.apply('__invalid_action__',{})
    return dict(task_id=case['id'],base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],
                split=case['split'],profile='action-choice/v2',seed=seed,calls=e.engine.state['calls'],
                invalid_decisions=errors,events=rows,terminal=copy.deepcopy(e.engine.state['terminal']),
                success=e.engine.state['terminal']['success'] is True,
                scope='Full public-card choice episode with physical/operational terminal;not the raw native-tool profile')


def catalog_control(q):
    """Explicit public algorithmic control, never substituted for a learned policy."""
    card=q['card']
    if not card['validated']:a=dict(name='validate',arguments={})
    elif not card['has_fresh_measurements']:a=dict(name='simulate',arguments={})
    else:
        overloaded=[r for r in card['study']['branches'] if r['loading_pct'] is not None and r['loading_pct']>100.]
        if overloaded:
            line=max(overloaded,key=lambda r:r['loading_pct']);catalog=max(card['scene']['catalog'],key=lambda c:float(c['rating_a']))
            a=dict(name='set_line_catalog',arguments=dict(equipment_id=line['id'],catalog_id=catalog['id']))
        else:a=dict(name='submit',arguments={})
    return json.dumps(dict(choice=next(m['choice'] for m in q['choices'] if m['action']==a)))

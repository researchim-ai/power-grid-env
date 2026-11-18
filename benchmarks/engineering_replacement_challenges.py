"""Replacement profile v2 closes remove/add bypass of the catalog-call budget.

The public limit counts distinct required routes whose equipment was changed,
regardless of whether a candidate edits a passport or removes/rebuilds a line.
Historical catalog-intervention/v1 results remain unchanged.
"""
import copy,json
from pathlib import Path
from benchmarks.engineering_catalog_challenges import CatalogEpisode
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


class ReplacementEpisode(CatalogEpisode):
    def __init__(self,case):
        super().__init__(case);self.changed_routes=set()
    def observe(self):
        card,aliases=super().observe();card['profile']='catalog-replacement/v2'
        card['goal']['maximum_changed_routes']=1
        card['changed_routes']=sorted(self.changed_routes)
        return card,aliases
    def apply(self,action):
        card,_=self.observe();route=None;name=action.get('name');args=action.get('arguments',{})
        if name=='add_line':route=args.get('route_id')
        elif name in ('remove_line','set_line_catalog'):
            route=next((r['id'] for r in card['scene']['routes'] if r['installed_line']==args.get('equipment_id')),None)
        reply,raw=super().apply(action)
        if reply.get('ok') is True and raw:
            if raw['name'] in ('add_line','remove_line','set_line_catalog'):
                if route is None:raise ValueError('accepted equipment mutation lacks public route attribution')
                self.changed_routes.add(route)
            if raw['name']=='submit' and len(self.changed_routes)>1:
                t=reply['result'];t.update(success=False,reward=0.,violations=t['violations']+['maximum_changed_routes'])
                self.engine.state['terminal']=copy.deepcopy(t)
        return reply,raw
    def readiness_label(self):
        r=super().readiness_label();r['solution_ready']=r['solution_ready'] and len(self.changed_routes)<=1;return r


def rebuild_all_control(q):
    """Adversarial public control:change all lines through remove/add only."""
    c=q['card'];safe=max(c['scene']['catalog'],key=lambda r:float(r['rating_a']))
    missing=next((r for r in c['scene']['routes'] if r['installed_line']=='absent'),None)
    if missing:a=dict(name='add_line',arguments=dict(route_id=missing['id'],catalog_id=safe['id']))
    else:
        bad=next((l for l in c['scene']['lines'] if float(l['rating_a'])<float(safe['rating_a'])),None)
        if bad:a=dict(name='remove_line',arguments=dict(equipment_id=bad['id']))
        elif not c['validated']:a=dict(name='validate',arguments={})
        elif not c['has_fresh_measurements']:a=dict(name='simulate',arguments={})
        else:a=dict(name='submit',arguments={})
    return json.dumps(dict(choice=next(m['choice'] for m in q['choices'] if m['action']==a)))


def audit(source,anchor,out):
    from benchmarks.engineering_choice_episode import run,catalog_control
    from benchmarks.engineering_skill_tasks import identity
    if verify(source)!=anchor:raise ValueError('owner catalog source differs')
    root=Path(out)
    if root.exists():raise ValueError('fresh replacement audit required')
    root.mkdir(parents=True);rows=[]
    for p in sorted((Path(source)/'cases').glob('*.json')):
        case=read_json(p)['case'];case_rows={}
        for name,policy in [('expert',catalog_control),('rebuild_all',rebuild_all_control)]:
            result=run(case,policy,episode_factory=ReplacementEpisode);case_rows[name]=result
        # Historical profile is replayed too, so the claimed bypass is observed.
        legacy=run(case,rebuild_all_control,catalog=True)
        if not legacy['success'] or not case_rows['expert']['success'] or case_rows['rebuild_all']['success'] or case_rows['rebuild_all']['terminal'].get('physics_success') is not True:
            raise ValueError('replacement profile controls failed')
        record=dict(case=case,expert=case_rows['expert'],rebuild_all=case_rows['rebuild_all'],legacy_rebuild_all=legacy)
        write_json(root/'cases'/(case['id']+'.json'),record);rows.append(record)
    if not rows:raise ValueError('nonempty replacement cases required')
    result=dict(schema='power-grid-env.replacement-challenge-audit/v2',source_manifest_sha256=anchor,cases=len(rows),base_networks=len({r['case']['base_project_id'] for r in rows}),
        expert_successes=sum(r['expert']['success'] for r in rows),rebuild_all_successes=sum(r['rebuild_all']['success'] for r in rows),
        historical_rebuild_all_successes=sum(r['legacy_rebuild_all']['success'] for r in rows),
        source_sha256={f:identity(Path(__file__).with_name(f)) for f in ('engineering_replacement_challenges.py','engineering_choice_episode.py')},
        eligible_for_gold_sft=False,scope='Public route-mutation limit with physics-completed adversarial controls;old v1 source is historical,not current gold training data')
    write_json(root/'summary.json',result);seal(root);return result

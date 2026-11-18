"""Physics-required catalog choices with one permitted replacement.

Synthetic passports,balanced constant-Z train networks. A permitted catalog
entry can still violate ampacity. Neither default catalog nor upgrading every
line solves the declared operational task. No economic objective is used.
"""
import copy
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_card_curriculum import CardEpisode
from benchmarks.grid_editor import solve
from benchmarks.engineering_reference import compare_reference
from benchmarks import engineering_agent as legacy
from power_grid_env.study_artifacts import seal


class CatalogEpisode(CardEpisode):
    def __init__(self,scenario):super().__init__(scenario);self.edits=0
    def observe(self):
        card,aliases=super().observe();card['profile']='catalog-intervention/v1';card['goal']['maximum_catalog_edits']=1;card['catalog_edits_used']=self.edits
        return card,aliases
    def apply(self,action):
        response,raw=super().apply(action)
        if raw and raw['name']=='set_line_catalog' and response.get('ok'):self.edits+=1
        if raw and raw['name']=='submit' and response.get('ok'):
            result=response['result'];result['physics_success']=result['success']
            if self.edits>1:result.update(success=False,reward=0.,violations=result['violations']+['maximum_catalog_edits'])
            self.engine.state['terminal']=copy.deepcopy(result)
        return response,raw
    def teacher(self):
        card,_=self.observe()
        if not card['validated']:return dict(name='validate',arguments={})
        if not card['has_fresh_measurements']:return dict(name='simulate',arguments={})
        overloaded=[r for r in card['study']['branches'] if r['loading_pct'] is not None and r['loading_pct']>100.]
        if overloaded:
            line=max(overloaded,key=lambda r:r['loading_pct']);catalog=max(card['scene']['catalog'],key=lambda c:float(c['rating_a']))
            return dict(name='set_line_catalog',arguments=dict(equipment_id=line['id'],catalog_id=catalog['id']))
        return dict(name='submit',arguments={})
    def readiness_label(self):
        r=super().readiness_label();r['solution_ready']=r['solution_ready'] and self.edits<=1;return r


def generate(base,index):
    case=copy.deepcopy(base);req=case['requirements'];project=copy.deepcopy(case['healthy_project'])
    initial=solve(project);compare_reference(project,initial)
    currents=sorted([(b['from_terminal']['current_a'],b['id']) for b in initial['branches']],reverse=True)
    if currents[0][0]<=currents[1][0]*1.03:raise ValueError('insufficient one-line separation;no resampling')
    rating=(currents[0][0]+currents[1][0])/2
    safe='copper-1' if index%2==0 else 'copper-2';unsafe='copper-2' if safe=='copper-1' else 'copper-1'
    low=dict(req['catalog']['copper-1'],rating_a=rating)
    high=dict(req['catalog']['copper-2'],rating_a=max(2*currents[0][0],req['catalog']['copper-2']['rating_a']))
    req['catalog']=dict(sorted({unsafe:low,safe:high}.items()));req['maximum_catalog_edits']=1
    for line in project['lines']:line.update(low)
    bad=solve(project);bad_audit=compare_reference(project,bad)
    if not legacy.physical_errors(bad,req):raise ValueError('no observable physical violation')
    overloaded=[b for b in bad['branches'] if b['loading_pct']>100.]
    if len(overloaded)!=1:raise ValueError('one replacement does not localize uniquely')
    target=overloaded[0]['id'];fixed=copy.deepcopy(project);next(l for l in fixed['lines'] if l['id']==target).update(high)
    good=solve(fixed);good_audit=compare_reference(fixed,good)
    if legacy.structure_errors(fixed,req) or legacy.physical_errors(good,req):raise ValueError('one-line replacement is not feasible')
    case.update(id='catalog-'+hashlib.sha256((base['base_project_id']+'catalog-intervention-v1:'+str(index%2)).encode()).hexdigest()[:24],kind='repair',project=project,healthy_project=fixed,truth=None,
        parameter_provenance='owner_synthetic_catalog_intervention_not_OEM')
    return case,dict(before=bad_audit,after=good_audit,target_line=target,safe_catalog=safe,unsafe_catalog=unsafe)


def run(case,policy='expert'):
    e=CatalogEpisode(case);rows=[]
    for _ in range(24):
        if policy=='expert':action=e.teacher()
        elif policy=='upgrade_all':
            card,_=e.observe();safe=max(card['scene']['catalog'],key=lambda c:float(c['rating_a']))['id']
            lines=card['scene']['lines']
            if e.edits<len(lines):action=dict(name='set_line_catalog',arguments=dict(equipment_id=lines[e.edits]['id'],catalog_id=safe))
            elif not card['validated']:action=dict(name='validate',arguments={})
            elif not card['has_fresh_measurements']:action=dict(name='simulate',arguments={})
            else:action=dict(name='submit',arguments={})
        else:raise ValueError('unknown control')
        card,_=e.observe();ready=e.readiness_label();reply,raw=e.apply(action);rows.append(dict(card=card,action=action,readiness=ready,response=reply))
        if e.engine.state['finished']:break
    return dict(task_id=case['id'],family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],split='train',policy=policy,events=rows,
        edits=e.edits,terminal=e.engine.state['terminal'],success=(e.engine.state['terminal'] or {}).get('success') is True)


def export(train,anchor,out):
    data=ProceduralDataset(train,anchor,strict_contract());root=Path(out)
    if root.exists():raise ValueError('fresh catalog challenge output required')
    root.mkdir(parents=True);bases={c['base_project_id']:c for c in data.scenarios.values()};records=[];quarantine=[]
    for base_id,base in sorted(bases.items()):
        for i in (0,1):
            try:
                case,evidence=generate(base,i);expert=run(case);negative=run(case,'upgrade_all')
                if not expert['success'] or negative['success'] or negative['terminal']['physics_success'] is not True:raise ValueError('expert/operational negative control failed')
                record=dict(case=case,evidence=evidence,expert=expert,negative=negative);records.append(record);write_json(root/'cases'/(case['id']+'.json'),record)
            except (ValueError,RuntimeError) as exc:quarantine.append(dict(base_project_id=base_id,reason=str(exc)))
    result=dict(schema='power-grid-env.catalog-intervention-corpus/v1',train_manifest_sha256=anchor,candidate_base_networks=len(bases),admitted=len(records),admitted_base_networks=len({r['case']['base_project_id'] for r in records}),paired_catalog_renamings=True,quarantined=quarantine,
        safe_catalog_counts={c:sum(r['evidence']['safe_catalog']==c for r in records) for c in ('copper-1','copper-2')},
        expert_successes=sum(r['expert']['success'] for r in records),upgrade_all_successes=sum(r['negative']['success'] for r in records),
        independent_physics_checked=True,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),eligible_for_gold_sft=False,
        scope='New one-replacement physical/operational teaching profile;synthetic catalog,train only;not model performance or general industrial qualification')
    write_json(root/'summary.json',result);seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser()
    for f in ('train','manifest-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(export(a.train,a.manifest_sha256,a.out)))

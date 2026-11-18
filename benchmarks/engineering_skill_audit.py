"""Visible-input deduplication and hierarchical sampling for isolated skills.

Alternative choice labels can describe successful plans. Contradictory labels
for deterministic readout/binding/readiness observations are rejected. Source
lineage is retained even when identical public prompts have several origins.
"""
import argparse
from collections import Counter, defaultdict
import json
import random
from pathlib import Path
from benchmarks.engineering_skill_tasks import SkillDataset,card_messages
from benchmarks.engineering_skill_pilot import sources
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal,read_json


def visible_identity(row, presentation_seed=420):
    if row['skill']=='perception':
        # Filename, family, private metadata and labels are not model observations.
        return digest(dict(skill='perception',question=row['question'],mode=row['mode'],
                           pixels=row['image_sha256'] if row['mode']=='image' else None)),row['answer']
    messages,answer=card_messages(row,presentation_seed)
    return digest(messages),answer


def stratum(row):
    skill=row['skill']
    if skill=='binding':return row['given_tool']
    if skill=='readiness':return json.dumps([row['card']['kind'],row['card']['has_fresh_measurements'],row['answer']],sort_keys=True)
    if skill=='perception':return '|'.join(row[k] for k in ('kind','view','mode','variant'))
    return row['card']['kind']+'|'+row['action']['name']


def inventory(data):
    groups=defaultdict(list)
    for i,row in enumerate(data.rows):
        key,answer=visible_identity(row);groups[key].append((i,digest(answer)))
    conflicts=[];alternatives=[]
    for key,values in groups.items():
        if len({v for _,v in values})>1:
            record=dict(visible_sha256=key,indices=[i for i,_ in values],labels=len({v for _,v in values}))
            if any(data.rows[i]['skill']!='choice' for i,_ in values):conflicts.append(record)
            else:alternatives.append(record)
    return dict(source_rows=len(data.rows),unique_visible_inputs=len(groups),
                unique_visible_targets=sum(len({v for _,v in values}) for values in groups.values()),
                repeated_visible_target_rows=sum(len(values)-len({v for _,v in values}) for values in groups.values()),
                deterministic_conflicts=conflicts,choice_alternative_targets=alternatives,
                by_skill=dict(Counter(r['skill'] for r in data.rows))),groups


def schedule(data,steps,seed):
    """Cycle skills uniformly; then family/base/stratum/unique visible pair.

    Selection uses teacher strata, not candidate outcomes. Label alternatives
    remain distinct; repeated source occurrences within a leaf are not weight.
    """
    if type(steps) is not int or not 1<=steps<=100000:
        raise ValueError('bounded positive sample count required')
    groups=defaultdict(lambda:defaultdict(lambda:defaultdict(lambda:defaultdict(dict))))
    scene_lineage={}
    for i,r in enumerate(data.rows):
        visible,answer=visible_identity(r);pair=(visible,digest(answer))
        base=r.get('base_project_id')
        if base is None:
            key=r['scene_sha256']
            if key not in scene_lineage:
                scene_lineage[key]=read_json(Path(data.sources['visual']['path'])/'scenes'/(key+'.json'))
            meta=scene_lineage[key]
            if meta['split']!='train' or meta['family_sha256']!=r['family_sha256']:
                raise ValueError('visual source lineage differs')
            base=meta['base_project_id']
        groups[r['skill']][r['family_sha256']][base][stratum(r)].setdefault(pair,i)
    if not groups:raise ValueError('nonempty skill dataset required')
    rng=random.Random(seed);skills=sorted(groups);result=[]
    while len(result)<steps:
        rng.shuffle(skills)
        for skill in skills:
            family=rng.choice(sorted(groups[skill]));bases=groups[skill][family]
            base=rng.choice(sorted(bases));strata=bases[base];leaf=strata[rng.choice(sorted(strata))]
            i=leaf[rng.choice(sorted(leaf))]
            result.append(dict(index=i,row_id=data.rows[i]['id'],skill=skill,family_sha256=family,base_project_id=base))
            if len(result)==steps:break
    return result


def run(source_spec,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh visible skill audit required')
    data=SkillDataset(source_spec);summary,_=inventory(data)
    if summary['deterministic_conflicts']:raise ValueError('contradictory deterministic teaching labels')
    proposed=schedule(data,256,420)
    result=dict(schema='power-grid-env.visible-skill-audit/v1',sources=source_spec,**summary,
                proposed_schedule=proposed,schedule_counts=dict(Counter(x['skill'] for x in proposed)),
                optimizer_run=False,scope='Visible-input audit and proposed balanced schedule;choice alternatives require upstream physical replay;not admitted optimizer recipe or model performance')
    write_json(root/'summary.json',result);seal(root);return {k:v for k,v in result.items() if k not in ('proposed_schedule','choice_alternative_targets')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    print(json.dumps(run(sources(),a.out)))

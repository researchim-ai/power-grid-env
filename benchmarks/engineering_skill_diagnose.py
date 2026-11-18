"""Posthoc skill error attribution; never promotes diagnostic parses to passes."""
import argparse
from pathlib import Path
import re
from benchmarks.engineering_skill_tasks import SkillDataset,shuffled_menu,score
from benchmarks.engineering_rollout import strict_json
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def content(raw,expected,skill):
    match=re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*',raw,re.S)
    text=match[1] if match else raw
    try:value=strict_json(text)
    except (ValueError,TypeError):value=None
    wrapped=skill=='binding' and isinstance(value,dict) and isinstance(value.get('arguments'),dict)
    diagnostic=value['arguments'] if wrapped else value
    from benchmarks.engineering_grounding import digest
    return dict(single_fence=match is not None,binding_wrapper=wrapped,
                diagnostic_content_exact=digest(diagnostic)==digest(expected),
                strict=score(raw,expected),operational_score_unchanged=True)


def run(plan,plan_anchor,result,result_anchor,out):
    if verify(plan)!=plan_anchor or verify(result)!=result_anchor:raise ValueError('owner skill receipts differ')
    p=read_json(Path(plan)/'summary.json');r=read_json(Path(result)/'summary.json')
    if r.get('complete') is not True or r.get('plan_manifest_sha256')!=plan_anchor:raise ValueError('complete matching skill pilot required')
    data=SkillDataset(p['sources']);indices={e['row_id']:e['index'] for e in p['exemplars']};rows=[]
    for candidate,key in [('base','before'),('adapter32','after')]:
        for value in r[key]:
            row=dict(value,diagnosis=content(value['completion'],value['expected'],value['skill']))
            if value['skill']=='choice':
                source=data.rows[indices[value['row_id']]];menu=shuffled_menu(source['card'],value['presentation_seed'])
                gold=next(m['action'] for m in menu if m['choice']==value['expected']['choice'])
                try:chosen=strict_json(value['completion']).get('choice')
                except (ValueError,TypeError,AttributeError):chosen=None
                row.update(expected_action=gold,selected_action=next((m['action'] for m in menu if m['choice']==chosen),None))
            rows.append(row)
    summary=dict(schema='power-grid-env.skill-error-diagnosis/v1',plan_manifest_sha256=plan_anchor,result_manifest_sha256=result_anchor,rows=rows,
        strict_correct={c:sum(x['exact'] for x in rows if x['candidate']==c) for c in ('base','adapter32')},
        diagnostic_content_correct={c:sum(x['diagnosis']['diagnostic_content_exact'] for x in rows if x['candidate']==c) for c in ('base','adapter32')},
        original_scores_unchanged=True,scope='Posthoc known-fence/binding-wrapper diagnosis and public choice action resolution;not policy success or automatic output repair')
    write_json(Path(out)/'summary.json',summary);seal(out);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('plan','plan-manifest-sha256','result','result-manifest-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();r=run(a.plan,a.plan_manifest_sha256,a.result,a.result_manifest_sha256,a.out)
    print({k:v for k,v in r.items() if k!='rows'})

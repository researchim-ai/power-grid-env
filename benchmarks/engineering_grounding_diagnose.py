"""Distinguish numeric perception from strict-output compliance.

Diagnostic fence removal never changes the original operational score.
"""
import re
from benchmarks.engineering_grounding_runtime import score
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def diagnose(raw,expected):
    strict=score(raw,expected);match=re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*',raw,re.S)
    body=score(match[1],expected) if match else strict
    return dict(strict=strict,diagnostic_unwrapped_content=body,single_fence=match is not None,operational_success=strict['semantic'])


def report(root,anchor,out):
    if verify(root)!=anchor:raise ValueError('owner heldout evaluation differs')
    r=read_json(str(root)+'/summary.json');rows=[]
    for row in r['results']:rows.append(dict(**row,diagnosis=diagnose(row['completion'],row['expected'])))
    pairs={}
    for candidate in ('base','adapter65'):
        groups={x['base_project_id'] for x in rows if x['candidate']==candidate}
        pairs[candidate]=dict(strict=sum(all(x['diagnosis']['operational_success'] for x in rows if x['candidate']==candidate and x['base_project_id']==b) for b in groups),
            diagnostic_content=sum(all(x['diagnosis']['diagnostic_unwrapped_content']['semantic'] for x in rows if x['candidate']==candidate and x['base_project_id']==b) for b in groups),total=len(groups))
    result=dict(schema='power-grid-env.grounding-failure-diagnosis/v1',source_manifest_sha256=anchor,pairs=pairs,rows=rows,original_scores_unchanged=True,
        scope='Strict format versus diagnostic numeric content;unwrapped text is not promoted into successful actions')
    write_json(str(out)+'/summary.json',result);seal(out);return result

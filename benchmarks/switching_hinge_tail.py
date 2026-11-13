"""Assess one half-step extension of a sealed fixed-mesh qualification.

No new simulation or tolerance tuning. This is production-solver Cauchy
self-consistency, not an independent or absolute continuous-time certificate.
"""
import argparse
from pathlib import Path
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify
from power_grid_env.switching_drive import SwitchingDriveStudy
from power_grid_env.switching_convergence import SwitchingTemporalLimits,differences,audited


def compatible(previous,next_study):
    a=SwitchingDriveStudy.model_validate(previous);b=SwitchingDriveStudy.model_validate(next_study)
    if a.adaptive is not None or b.adaptive is not None:raise ValueError('fixed mesh extension requires adaptive=None')
    x=a.model_dump(mode='json');y=b.model_dump(mode='json');ha=x.pop('max_step_s');hb=y.pop('max_step_s')
    if x!=y or abs(ha/2-hb)>1e-15*ha:raise ValueError('extension must halve only max_step_s')
    return b


def assess(base,fine):
    base=Path(base);fine=Path(fine);identities=dict(base=verify(base),fine=verify(fine))
    summary=read_json(base/'summary.json');result=read_json(fine/'result.json')
    if summary['code_sha256']!=result['code_sha256'] or summary['code_sha256']!=package_fingerprint():raise ValueError('extension producer differs')
    attempts=summary['attempts']
    if len(attempts)<2 or any(a['status']!='completed' for a in attempts[-2:]):raise ValueError('extension requires two completed adjacent meshes')
    indexes=[a['index'] for a in attempts[-2:]]
    if indexes[1]!=indexes[0]+1:raise ValueError('nonadjacent prior meshes')
    paths=[base/f'level-{i}' for i in indexes]+[fine]
    docs=[read_json(p/'study.json') for p in paths]
    compatible(docs[0],docs[1]);study=compatible(docs[1],docs[2])
    results=[read_json(p/'result.json') for p in paths];limits=SwitchingTemporalLimits.model_validate(summary['limits'])
    audits=[audited(study,r,limits) for r in results]
    old=differences(study,results[0],results[1],limits);new=differences(study,results[1],results[2],limits)
    reduction={}
    for k,v in new['groups'].items():
        a=old['groups'][k]['maximum_normalized'];b=v['maximum_normalized']
        reduction[k]=dict(previous_normalized=a,current_normalized=b,passed=b<=max(1e-8,limits.reduction_factor*a))
    qualified=new['passed'] and all(v['passed'] for v in reduction.values()) and all(all(v.values()) for v in audits)
    return dict(schema='power-grid-env.switching-fixed-extension/v1',qualified=qualified,selected_step_s=study.max_step_s if qualified else None,code_sha256=summary['code_sha256'],limits=limits.model_dump(mode='json'),input_manifests=identities,physics_audits=audits,difference=new,reduction=reduction,absolute_error_certified=False,independent_reference=False,scope='one half-step extension, same producer and identical study except mesh size; three audited fixed meshes and bounded decreasing Cauchy differences')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',required=True);parser.add_argument('--fine',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();r=assess(args.base,args.fine);Path(args.out).write_bytes(encoded(r));raise SystemExit(0 if r['qualified'] else 2)

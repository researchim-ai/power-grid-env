"""Parallel physical corpus plus exact mixed sequential/parallel equivalence."""
from pathlib import Path
import argparse
from power_grid_env.campaign import CampaignPlan,run_campaign,verify_campaign
from power_grid_env.study_artifacts import read_json
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import sha256_file,write_json
from benchmarks.campaign_acceptance import examples,qualify


def run(out,wheel):
    out=Path(out)
    if out.exists():raise ValueError('fresh parallel acceptance directory required')
    out.mkdir(parents=True);code=package_fingerprint();sources={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    full=examples(2)
    selected=['aalto-linear-fan__restart','aalto-saturated-viscous-thermal__temperature','sensitivity-ats_delay','sensitivity-uel_bound','islands-radau-matched','islands-network_dae-mismatch']
    cases=[c.model_copy(deep=True) for c in full.cases if c.id in selected]
    for case in cases:case.memory_limit_mb=1024
    serial=CampaignPlan(id='mixed-equivalence',cases=cases,maximum_workers=1)
    parallel=serial.model_copy(update={'maximum_workers':3})
    write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=sources,corpus=full.model_dump(mode='json'),serial=serial.model_dump(mode='json'),parallel=parallel.model_dump(mode='json'),claim='physical reproducibility and bounded admissions; no speed advantage claimed'))
    # Run the expensive original-reference acceptance first, preserving all raw
    # native traces and its exact producer/input/inventory checks.
    corpus=qualify(out/'corpus',wheel,maximum_workers=2)
    a=run_campaign(serial.model_dump(mode='json'),out/'serial');verify_campaign(out/'serial')
    b=run_campaign(parallel.model_dump(mode='json'),out/'parallel');verify_campaign(out/'parallel')
    checks=[]
    for case in cases:
        left=read_json(out/'serial/attempts'/case.id/'result.json');right=read_json(out/'parallel/attempts'/case.id/'result.json')
        keys=['parameters','baseline_outputs','event_sequence','available'] if case.kind=='sensitivity' else ['time_s','channels','event_log']
        if case.kind=='protected_motor':keys+=['engineering']
        check=dict(case=case.id,keys=keys,passed=all(left[k]==right[k] for k in keys));checks.append(check);print('equivalence',case.id,check['passed'],flush=True)
    unchanged=code==package_fingerprint() and sources=={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    result=dict(passed=bool(corpus['passed'] and a['passed'] and b['passed'] and all(c['passed'] for c in checks) and b['scheduler']['peak_active_workers']==3 and unchanged),code_sha256=code,producer_unchanged=unchanged,physical_comparisons=16,equivalence=checks,corpus_scheduler=read_json(out/'corpus/run/summary.json')['scheduler'],equivalence_scheduler=b['scheduler'],external_host_verified=False,industrial_release_qualified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file()})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);a=p.parse_args()
    raise SystemExit(0 if run(a.out,a.wheel)['passed'] else 1)

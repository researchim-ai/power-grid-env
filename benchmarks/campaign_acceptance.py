"""Whole-campaign acceptance: physical peers, continuation, portable data only."""
from pathlib import Path
import argparse,json,os
from power_grid_env.campaign import CampaignPlan,CampaignCase,Rule,run_campaign,verify_campaign,expand_grid
from power_grid_env.study_artifacts import read_json,export_zip,extract_zip
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file


def examples(maximum_workers=1):
    from benchmarks.protected_motor_cases import NAMES,case
    from benchmarks.full_sensitivity_cases import NAMES as directions,case as derivative,pv_oracle
    from benchmarks.long_islands import case as island
    cases=[]
    for name in NAMES:
        study=case(name);classification='accelerated' if name.endswith('__restart') else 'protected_trip'
        cases.append(CampaignCase(id=name,kind='protected_motor',study=study.model_dump(mode='json'),rules=[Rule(path='/engineering/classification',operator='eq',value=classification)],worker_wall_s=180.))
    template=next(c for c in cases if c.id=='aalto-saturated-fan__locked')
    grid=expand_grid(dict(id='relay-grid',template=template.model_dump(mode='json'),axes=[dict(path='/relay/trip_level',values=[3.,7.],unit='I2 pu'),dict(path='/relay/time_constant_s',values=[.5,1.5],unit='s')]))
    cases.extend(grid.cases)
    for name in directions:
        study,request,expected=derivative(name);request.isolate=False
        wanted=pv_oracle()[0] if name=='pv_initialization' else expected
        cases.append(CampaignCase(id='sensitivity-'+name,kind='sensitivity',study=study.model_dump(mode='json'),protocol=request.model_dump(mode='json'),rules=[Rule(path='/parameters/0/derivative/0',operator='ge',value=float(wanted)-2e-6),Rule(path='/parameters/0/derivative/0',operator='le',value=float(wanted)+2e-6)],worker_wall_s=180.))
    for mode in ('radau','network_dae'):
        for variant in ('matched','mismatch'):
            cases.append(CampaignCase(id='islands-'+mode+'-'+variant,kind='study',study=island(mode,variant).model_dump(mode='json'),worker_wall_s=180.))
    return CampaignPlan(id='industrial-campaign-21',cases=cases,maximum_wall_s=1800.,maximum_workers=maximum_workers)


def qualify(out,wheel,maximum_workers=1):
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.protected_motor_reference import reference as motor_reference,compare as motor_compare
    from benchmarks.long_islands import reference as island_reference,compare as island_compare
    from power_grid_env.protected_motor import ProtectedMotorStudy
    out=Path(out)
    if out.exists():raise ValueError('fresh campaign qualification required')
    out.mkdir(parents=True);code=package_fingerprint();producers={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    plan=examples(maximum_workers);write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=producers,native_sources=verify_native(Path(wheel)),plan=plan.model_dump(mode='json')))
    run=run_campaign(plan.model_dump(mode='json'),out/'run');verify_campaign(out/'run');print('campaign',run['passed'],len(run['cases']),flush=True)
    comparisons=[]
    for case in plan.cases:
        primary=read_json(out/'run/attempts'/case.id/'result.json')
        if case.kind=='protected_motor':
            native=motor_reference(ProtectedMotorStudy.model_validate(case.study));check=motor_compare(primary,native)
        elif case.kind=='study':
            native=island_reference('mismatch' if case.id.endswith('mismatch') else 'matched');check=island_compare(primary,native)
        else:continue
        write_json(out/'references'/case.id/'native.json',native);write_json(out/'references'/case.id/'comparison.json',check)
        comparisons.append(dict(case=case.id,passed=check['passed']));print(case.id,check['passed'],flush=True)
    resumed=run_campaign(plan.model_dump(mode='json'),out/'resumed',resume_from=out/'run');verify_campaign(out/'resumed')
    first=export_zip(out/'resumed',out/'campaign.zip');second=export_zip(out/'resumed',out/'repeat.zip')
    extracted=extract_zip(out/'campaign.zip',out/'imported');verify_campaign(extracted)
    restored=run_campaign(plan.model_dump(mode='json'),out/'restored',resume_from=extracted)
    same=all(read_json(out/'run/attempts'/c.id/'result.json')==read_json(out/'restored/attempts'/c.id/'result.json') for c in plan.cases)
    unchanged=code==package_fingerprint() and producers=={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    result=dict(passed=bool(run['passed'] and resumed['passed'] and restored['passed'] and all(r['reused'] for r in resumed['cases']) and same and first==second and all(c['passed'] for c in comparisons) and unchanged),cases=len(plan.cases),independent_physical_comparisons=comparisons,exact_restored_results=same,deterministic_archive=first==second,producer_unchanged=unchanged,code_sha256=code,external_host_verified=False,industrial_release_qualified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file()})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);a=p.parse_args()
    raise SystemExit(0 if qualify(a.out,a.wheel)['passed'] else 1)

"""Three motors on two load sites: new physics in the existing train family.

No held-out rewiring is relabeled into train. Catalog and requirements stay as
published in the original fixture; an engineering rejection remains a rejection.
"""
import argparse
import copy
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmarks.network_construction_cases import example, proposal
from benchmarks.network_construction_reference import reference
from power_grid_env.design_multimodal import topology_family, verify_dataset
from power_grid_env.evidence import package_fingerprint
from power_grid_env.network_design import compile_network, run_network_design, verify_network_design
from power_grid_env.network_design_view import observation, export_view
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest


def task():
    result = example(2)
    result['id'] = 'train-three-motors-two-sites'
    result['node_demand_kva']['n2'] = 150.
    for case in result['mandatory_cases']:
        cfg = case['study']['induction']
        motor = copy.deepcopy(cfg['motors'][1])
        motor['id'] = 'm3'
        cfg['motors'].append(motor)
        criterion = copy.deepcopy(case['motors'][1])
        criterion['motor_id'] = 'm3'
        case['motors'].append(criterion)
        if case['id'] == 'staged':
            cfg['events'].append(dict(time_s=.8, kind='motor_close', equipment_id='m3'))
        case['parameter_provenance'] += '\nAuthored third identical motor at existing n2 site; no new OEM data.'
    simultaneous = result['mandatory_cases'][0]
    for motor_id in ('m','m2','m3'):
        case = copy.deepcopy(simultaneous)
        case['id'] = 'open-reclose-'+motor_id
        case['study']['induction']['events'] = [
            dict(time_s=3.,kind='motor_open',equipment_id=motor_id),
            dict(time_s=3.2,kind='motor_close',equipment_id=motor_id)]
        result['mandatory_cases'].append(case)
    return result


def jobs():
    return [dict(id='three-on-two-'+kind, task=task(), proposal=proposal(kind,2)) for kind in ('star','chain')]


def execute(job, out):
    t,p,plan,c = compile_network(job['task'],job['proposal'])
    family = topology_family(observation(t,p))
    result = run_network_design(t,p,out)
    checked = verify_network_design(out, expected_task=t, expected_proposal=p)
    if not result['completed'] or not result['numerically_qualified']:
        raise ValueError('new train study incomplete or numerically unqualified: '+job['id'])
    ref = reference(t.model_dump(mode='json'),p.model_dump(mode='json'),c,plan.model_dump(mode='json'))
    if not ref['passed']:raise ValueError('independent SI/OpenDSS reference failed')
    openings=[]
    for case in plan.cases:
        if not case.id.startswith('open-reclose-'):continue
        motor_id = case.id.removeprefix('open-reclose-')
        trace=read_json(Path(out)/'studies'/case.id/'grid-2'/'result.json')
        times,sides=trace['time_s'],trace['sample_side'];channels=trace['channels']
        def index(t,side):
            found=[i for i,(a,b) in enumerate(zip(times,sides)) if abs(a-t)<1e-10 and b==side]
            if len(found)!=1:raise ValueError('exact event side absent')
            return found[0]
        pre,post,close = index(3.,'pre_event'),index(3.,'post_event'),index(3.2,'post_event')
        speed=channels[motor_id+'.speed_pu'][pre]
        if (not speed >= .9 or channels[motor_id+'.connected'][post] != 0 or
                channels[motor_id+'.connected'][close] != 1 or
                abs(channels[motor_id+'.current_pu'][post]) > 1e-10):
            raise ValueError('running motor opening/reconnection profile not achieved')
        openings.append(dict(motor_id=motor_id, speed_before_open=speed,
            speed_after_open=channels[motor_id+'.speed_pu'][post],
            current_after_open=channels[motor_id+'.current_pu'][post]))
    export_view(t,p,Path(out).with_name(Path(out).name+'-view'))
    print(job['id'],result['status'],'running openings',len(openings),flush=True)
    return dict(id=job['id'],family_id=family,status=result['status'],
        engineering_accepted=result['meets_declared_requirements'],design_manifest=checked['manifest_sha256'],
        running_openings=openings,reference=ref)


def run(corpus, corpus_manifest, out):
    old=verify_dataset(corpus,corpus_manifest)
    train={f['id'] for f in old['families'] if f['split']=='train'}
    heldout={f['id'] for f in old['families'] if f['split']!='train'}
    declared=jobs()
    for job in declared:
        t,p,plan,c=compile_network(job['task'],job['proposal'])
        family=topology_family(observation(t,p))
        if family not in train or family in heldout:raise ValueError('new design must remain in old train family')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    producer=package_fingerprint();builder=digest(Path(__file__).read_bytes())
    with ProcessPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(execute,declared,[root/j['id'] for j in declared]))
    result=dict(passed=True,code_sha256=producer,builder_sha256=builder,
        parent_corpus_manifest_sha256=corpus_manifest,designs=2,dynamic_runs=36,
        mandatory_cases_per_design=6,independent_fault_solutions=6,rows=rows,
        test_relabelled=False,independent_dynamic_reference=False,manufacturer_authenticated=False,
        scope='Two new P3 train designs,three motors on two sites; 36 three-grid motor studies, '
              'SI nodal and passive OpenDSS faults, explicit rotating contactor openings; no external dynamic oracle.')
    if package_fingerprint()!=producer or verify(corpus)!=corpus_manifest or digest(Path(__file__).read_bytes())!=builder:
        raise ValueError('source/runtime changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for k in ('corpus','corpus-manifest','out'):parser.add_argument('--'+k,required=True)
    print(run(**vars(parser.parse_args())))

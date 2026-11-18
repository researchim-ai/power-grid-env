"""Sealed, bounded OC/actuator/I2/limiter qualification; no external services."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.topology_stator_reference import TopologyStatorReference
from power_grid_env.study_artifacts import encoded, seal, digest


def event_key(e):
    check=e.get('synchronism',{})
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value','outcome','cause','breaker_id','threshold'))+(check.get('allowed'),check.get('reason'))


def compare(reference, report):
    if not report.get('completed'):return dict(passed=False,incomplete_native=True)
    if [event_key(e) for e in reference.events]!=[event_key(e) for e in report['event_log']]:
        return dict(passed=False,event_identities_match=False)
    result=waveform_compare(reference,report);errors={};metadata=True
    for a,b in zip(reference.events,report['event_log']):
        if 'due_s' in a:metadata=metadata and abs(a['due_s']-b.get('due_s',float('inf')))<=2e-7
        if 'synchronism' not in a:continue
        x,y=a['synchronism'],b.get('synchronism',{})
        if x.keys()!=y.keys():metadata=False;continue
        for k,v in x.items():
            if k in ('reason','allowed') or v is None:
                metadata=metadata and v==y[k];continue
            if y[k] is None:metadata=False;continue
            error=abs(v-y[k]);errors[k]=max(errors.get(k,0.),error)
            metadata=metadata and error<=(5e-5 if k.endswith('_hz') else 2e-6)
    result.update(synchronism_errors=errors,event_metadata_match=bool(metadata))
    result['passed']=bool(result['passed'] and metadata)
    return result


def coverage(events,scenario):
    if any(e['kind'].startswith(('generator_thermal','network_relay')) for e in events):raise ValueError('inactive protection expected')
    actions=[e for e in events if e['kind'] in ('breaker_open','breaker_close') and e.get('cause')=='manual']
    expected=[('backup-cb','breaker_open','applied'),('primary-cb','breaker_open','applied'),('backup-cb','breaker_close','applied'),('primary-cb','breaker_close','applied')] if scenario=='dual_cycle' else [('primary-cb' if scenario=='line_cycle' else 'backup-cb','breaker_open','applied'),('primary-cb' if scenario=='line_cycle' else 'backup-cb','breaker_close','blocked' if scenario=='source_blocked' else 'applied')]
    if [(e['equipment_id'],e['kind'],e['outcome']) for e in actions]!=expected:raise ValueError('topology outcome sequence differs')
    for action in actions:
        requests=[e for e in events if e['kind']==action['kind']+'_requested' and e['equipment_id']==action['equipment_id']]
        queued=[e for e in events if e['kind']==action['kind'] and e['equipment_id']==action['equipment_id'] and e.get('outcome')=='queued']
        if len(requests)!=1 or len(queued)!=1 or requests[0]['time_s']!=queued[0]['time_s'] or requests[0]['cause']!='manual' or abs(requests[0]['due_s']-requests[0]['time_s']-.005)>1e-12 or action['time_s']!=requests[0]['due_s']:raise ValueError('command/actuator delay differs')
    checks=[e['synchronism'] for e in actions if e['kind']=='breaker_close']
    reasons=['dead_receiver','synchronized'] if scenario=='dual_cycle' else ['frequency_difference' if scenario=='source_blocked' else 'synchronized']
    if [c['reason'] for c in checks]!=reasons or any(c['allowed']!=(c['reason']!='frequency_difference') for c in checks):raise ValueError('synchronism reason differs')
    for group in ('TGOV1','SEXS'):
        bounds={e.get('bound') for e in events if e.get('controller')==group and e['kind']=='controller_limit_enter'}
        if bounds!={'upper','lower'}:raise ValueError('missing two-sided saturation:'+group)
    return dict(openings=sum(e['kind']=='breaker_open' for e in actions),closings=sum(e['kind']=='breaker_close' and e['outcome']=='applied' for e in actions),blocked_closings=sum(e['outcome']=='blocked' for e in actions),dead_receiver_closings=sum(c['reason']=='dead_receiver' for c in checks))


def producers():
    from benchmarks.hybrid_stator_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/topology_stator_cases.py','benchmarks/topology_stator_reference.py',
              'benchmarks/topology_stator_acceptance.py'):
        result[p] = digest(Path(p).read_bytes())
    return result


def reference_packet(r):
    times = [i*.001 for i in range(501) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
    return dict(initialization=r.initial,events=r.events,
        regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times],
        one_sided_values=[dict(time_s=t,side=side,channels=r.sample(t,side))
            for t in sorted({e['time_s'] for e in r.events}) for side in ('pre_event','post_event')])


def execute(job, root):
    from power_grid_env.study_spec import StudySpec, run_study
    root.mkdir(parents=True,exist_ok=False)
    refs = [TopologyStatorReference(job['study']).run(max_step=.002),
            TopologyStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    for r in refs: coverage(r.events,job['scenario'])
    rows = []
    for method in ('radau','network_dae'):
        for grid in (0,1):
            s=copy.deepcopy(job['study']);s['coupled'].update(integration_method=method,step_s=.002/2**grid)
            owner=StudySpec.model_validate(s);report=run_study(owner)
            if not report.get('completed'):
                (root/('incomplete-'+method+'-'+str(grid)+'.json')).write_bytes(encoded(report))
                raise ValueError('native integration failed: '+str(report.get('solver_message',report.get('reason'))))
            checks=[compare(r,report) for r in refs]
            effects=coverage(report['event_log'],job['scenario'])
            leaf=root/(method+'-'+str(grid));leaf.mkdir()
            for name,value in [('study',owner.model_dump(mode='json')),('trace',report),('comparison',checks)]:
                (leaf/(name+'.json')).write_bytes(encoded(value))
            seal(leaf)
            rows.append(dict(method=method,grid=grid,passed=report['completed'] and report['additional_validation']['ok'] and all(c['passed'] for c in checks),coverage=effects))
    for name,r in zip(('reference','refined-reference'),refs):
        (root/(name+'.json')).write_bytes(encoded(reference_packet(r)))
    result=dict(id=job['id'],passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    print(job['id'],result['passed'],flush=True)
    return result


def qualify(out):
    from benchmarks.topology_stator_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==10 and all(r['passed'] for r in rows),physical_cases=10,
        native_runs=40,independent_trajectories=20,comparisons=80,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored controlled GENCLS/two-cage R(T), physical heat and energy with source/line manual opening, generator-fed island, isolated empty upstream node, staged dead-receiver restoration, allowed/blocked synchrochecked closing. No active OC/UV, backup/ATS/automatic reclose, multiple live islands, unbalanced faults, OEM or arbitrary simultaneous roots.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)

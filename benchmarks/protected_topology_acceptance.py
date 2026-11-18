"""Sealed active OC/UV, backup and one-shot reclose qualification."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.protected_topology_reference import ProtectedTopologyReference
from power_grid_env.study_artifacts import encoded, seal, digest


def compact_comparisons(checks):
    """Keep all gates/errors; reference packets already retain full waveforms."""
    return [{k:v for k,v in check.items() if k!='values'} for check in checks]


def event_key(e):
    check=e.get('synchronism',{})
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value','outcome','cause','breaker_id','threshold'))+(check.get('allowed'),check.get('reason'),e.get('reason'))


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
    if any(e['kind']=='generator_thermal_trip' for e in events):raise ValueError('diagnostic I2 must not trip')
    trips=[e for e in events if e['kind']=='network_relay_trip']
    cycling=scenario in ('definite_reclose','standard_inverse','very_inverse','extremely_inverse')
    if len(trips)!=(2 if cycling else 1) or any(e['equipment_id']!='primary-oc' or e['breaker_id']!='primary-cb' for e in trips):raise ValueError('relay trip count/target differs')
    actions=[e for e in events if e['kind']=='breaker_open' and e.get('cause')=='relay:primary-oc']
    if len(actions)!=len(trips) or any(e['outcome']!=('failed_to_operate' if scenario=='failure_backup' else 'applied') for e in actions):raise ValueError('primary operation differs')
    requests=[e for e in events if e['kind']=='breaker_open_requested' and e.get('cause')=='relay:primary-oc']
    if len(requests)!=len(trips):raise ValueError('trip request count differs')
    for trip,request,action in zip(trips,requests,actions):
        if trip['time_s']!=request['time_s'] or request['equipment_id']!='primary-cb' or abs(request['due_s']-trip['time_s']-.005)>1e-12 or action['time_s']!=request['due_s']:raise ValueError('trip/actuator delay differs')
    closes=[e for e in events if e['kind']=='breaker_close' and e.get('cause')=='automatic_reclose']
    expected_reclose=scenario not in ('failure_backup','undervoltage')
    if len(closes)!=int(expected_reclose):raise ValueError('one-shot reclose count differs')
    if closes:
        e=closes[0]
        if abs(e['time_s']-actions[0]['time_s']-.04)>1e-12:raise ValueError('reclose dead time differs')
        if e['outcome']!=('applied' if cycling else 'blocked'):raise ValueError('reclose outcome differs')
        if scenario=='manual_inhibit' and (e.get('reason')!='manual_inhibit' or 'synchronism' in e):raise ValueError('manual inhibit not honored before synchronism')
        if scenario=='sync_blocked' and e.get('synchronism',{}).get('reason')!='frequency_difference':raise ValueError('frequency blocking reason differs')
        if cycling and e.get('synchronism',{}).get('reason')!='synchronized':raise ValueError('live reconnection check differs')
    failures=[e for e in events if e['kind']=='breaker_failure_confirmed']
    backups=[e for e in events if e['kind']=='breaker_open' and e.get('cause')=='breaker_failure:primary-cb']
    if len(failures)!=int(scenario=='failure_backup') or len(backups)!=len(failures):raise ValueError('backup coverage differs')
    if failures:
        request=next(e for e in events if e['kind']=='breaker_open_requested' and e.get('cause')=='breaker_failure:primary-cb')
        if failures[0]['equipment_id']!='primary-cb' or abs(failures[0]['time_s']-actions[0]['time_s']-.01)>1e-12 or request['time_s']!=failures[0]['time_s'] or backups[0]['equipment_id']!='backup-cb' or backups[0]['outcome']!='applied' or abs(backups[0]['time_s']-request['time_s']-.005)>1e-12:raise ValueError('failure backup causality differs')
    for group in ('TGOV1','SEXS'):
        if not any(e.get('controller')==group and e['kind']=='controller_limit_enter' for e in events):raise ValueError('missing limiter coverage')
    return dict(relay_trips=len(trips),primary_applied=sum(e['outcome']=='applied' for e in actions),primary_failed=sum(e['outcome']=='failed_to_operate' for e in actions),reclose_applied=sum(e['outcome']=='applied' for e in closes),reclose_blocked=sum(e['outcome']=='blocked' for e in closes),backup_applied=len(backups))


def producers():
    from benchmarks.topology_stator_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/protected_topology_cases.py','benchmarks/protected_topology_reference.py',
              'benchmarks/protected_topology_acceptance.py'):
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
    refs = [ProtectedTopologyReference(job['study']).run(max_step=.002),
            ProtectedTopologyReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
            for name,value in [('study',owner.model_dump(mode='json')),('trace',report),('comparison',compact_comparisons(checks))]:
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
    from benchmarks.protected_topology_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==18 and all(r['passed'] for r in rows),physical_cases=18,
        native_runs=72,independent_trajectories=36,comparisons=144,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored radial GENCLS/two-cage R(T), physical heat/energy and controller limits with active generator OC (four curves), downstream UV, source backup after feeder failure, one-shot automatic reclose, retrip, manual-inhibit and synchronism blocking. No ATS, CT commands, multi-island, unbalanced faults, generator reclose, OEM or arbitrary root ties.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)

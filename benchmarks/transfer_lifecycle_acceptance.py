"""Supply measurement validity, explicit retry and interlocked return qualification."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.transfer_lifecycle_reference import TransferLifecycleReference
from power_grid_env.study_artifacts import encoded, seal, digest


def compact_comparisons(checks):
    """Keep all gates/errors; reference packets already retain full waveforms."""
    return [{k:v for k,v in check.items() if k!='values'} for check in checks]


def event_key(e):
    check=e.get('synchronism',{})
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value','outcome','cause','breaker_id','threshold','measurement_available','measurement_side'))+(check.get('allowed'),check.get('reason'),e.get('reason'))


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
            if isinstance(v,(str,bool)) or v is None:
                metadata=metadata and v==y[k];continue
            if y[k] is None:metadata=False;continue
            error=abs(v-y[k]);errors[k]=max(errors.get(k,0.),error)
            metadata=metadata and error<=(5e-5 if k.endswith('_hz') else 2e-6)
    result.update(synchronism_errors=errors,event_metadata_match=bool(metadata))
    result['passed']=bool(result['passed'] and metadata)
    return result


def coverage(events,scenario):
    starts=[e for e in events if e['kind']=='transfer_started']
    expected_starts=2 if scenario in ('pt_retry','repeat_inhibited') else 1
    if len(starts)!=expected_starts:raise ValueError('transfer cycle count differs')
    closes=[e for e in events if e['kind']=='breaker_close' and e.get('outcome')!='queued']
    blocks=[e for e in events if e['kind']=='transfer_blocked']
    pt=[e for e in events if e['kind'].startswith('transfer_') and '_measurement_' in e['kind']]
    for e in pt:
        side='primary' if 'primary' in e['kind'] else 'reserve'
        if e['measurement_available']!=e['kind'].endswith('_restored') or e['measurement_side']!=side:raise ValueError('PT availability metadata differs')
    if scenario=='primary_pt_pause':
        expected=.075+(1-.5*__import__('math').exp(-.02/.01))*.01
        if abs(starts[0]['time_s']-expected)>2e-10 or len([e for e in events if e['kind']=='transfer_detection_suspended'])!=1:raise ValueError('primary PT reset-memory timing differs')
    else:
        if abs(starts[0]['time_s']-.06)>2e-10:raise ValueError('initial detection differs')
    if scenario in ('reserve_pt_before_request','pt_retry'):
        if len(blocks)!=1 or blocks[0]['reason']!='reserve_measurement_unavailable' or abs(blocks[0]['time_s']-.075)>2e-10:raise ValueError('reserve PT early blocking differs')
    elif blocks:raise ValueError('unexpected early transfer block')
    if scenario=='reserve_pt_inflight':
        if len(closes)!=1 or closes[0]['outcome']!='blocked' or closes[0]['synchronism']['reason']!='reserve_measurement_unavailable' or abs(closes[0]['time_s']-.08)>2e-10:raise ValueError('in-flight close did not recheck PT')
    if scenario=='pt_retry':
        if abs(starts[1]['time_s']-.087)>2e-10 or len(closes)!=1:raise ValueError('explicit retry timing differs')
        repeated=[e for e in events if e['kind']=='breaker_open' and e.get('outcome')=='already_in_requested_state']
        if len(repeated)!=1 or abs(repeated[0]['time_s']-.092)>2e-10:raise ValueError('retry repeated physical opening')
    if scenario in ('retransfer','repeat_inhibited'):
        grid=[e for e in closes if e['equipment_id']=='grid-cb']
        if len(grid)!=2 or grid[0]['outcome']!='blocked' or grid[0]['synchronism'].get('reason')!='interlocked_breaker_closed' or grid[0]['synchronism'].get('interlock_id')!='reserve-cb' or grid[1]['outcome']!='applied':raise ValueError('return interlock/closing sequence differs')
        if abs(grid[0]['time_s']-.165)>2e-10 or abs(grid[1]['time_s']-.215)>2e-10:raise ValueError('return actuator delay differs')
        if scenario=='repeat_inhibited':
            if closes[-1]['outcome']!='blocked' or closes[-1].get('reason')!='manual_inhibit' or abs(closes[-1]['time_s']-.28)>2e-10:raise ValueError('ATS reset bypassed operator isolation')
    trips=[e for e in events if e['kind']=='network_relay_trip']
    if len(trips)!=int(scenario=='ct_during_transfer'):raise ValueError('reserve CT trip inventory differs')
    if trips and (trips[0]['equipment_id']!='reserve-oc' or abs(trips[0]['time_s']-.11)>2e-10):raise ValueError('CT restoration timing differs')
    return dict(transfer_starts=len(starts),early_blocks=len(blocks),reserve_closes=sum(e['equipment_id']=='reserve-cb' and e['outcome']=='applied' for e in closes),close_blocks=sum(e['outcome']=='blocked' for e in closes),grid_returns=sum(e['equipment_id']=='grid-cb' and e['outcome']=='applied' for e in closes),relay_trips=len(trips),pt_losses=sum(e['kind'].endswith('_lost') for e in pt))


def producers():
    from benchmarks.measurement_transfer_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/transfer_lifecycle_cases.py','benchmarks/transfer_lifecycle_reference.py',
              'benchmarks/transfer_lifecycle_acceptance.py'):
        result[p] = digest(Path(p).read_bytes())
    return result


def reference_packet(r):
    times = [i*.001 for i in range(round(r.study.coupled.duration_s/.001)+1) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
    return dict(initialization=r.initial,events=r.events,
        regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times],
        one_sided_values=[dict(time_s=t,side=side,channels=r.sample(t,side))
            for t in sorted({e['time_s'] for e in r.events}) for side in ('pre_event','post_event')])


def execute(job, root):
    from power_grid_env.study_spec import StudySpec, run_study
    root.mkdir(parents=True,exist_ok=False)
    refs = [TransferLifecycleReference(job['study']).run(max_step=.002),
            TransferLifecycleReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
    from benchmarks.transfer_lifecycle_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==18 and all(r['passed'] for r in rows),physical_cases=18,
        native_runs=72,independent_trajectories=36,comparisons=144,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored passport-temperature R(T) warm-standby ATS with primary/reserve PT availability, delayed-close validity recheck, reserve CT loss-restoration, explicit retry, scheduled manual return, interlock and persistent operator inhibition. No automatic retransfer policy, multi-island, joint standby heat initialization, OEM or arbitrary root ties.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)

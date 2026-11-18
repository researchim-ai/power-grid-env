"""Measured relay continuity and standby-transfer qualification."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.measurement_transfer_reference import MeasurementTransferReference
from power_grid_env.study_artifacts import encoded, seal, digest


def compact_comparisons(checks):
    """Keep all gates/errors; reference packets already retain full waveforms."""
    return [{k:v for k,v in check.items() if k!='values'} for check in checks]


def event_key(e):
    check=e.get('synchronism',{})
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value','outcome','cause','breaker_id','threshold','measurement_available'))+(check.get('allowed'),check.get('reason'),e.get('reason'))


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
    ats=scenario.startswith('ats_')
    if any(e['kind']=='generator_thermal_trip' for e in events):raise ValueError('diagnostic I2 must not trip')
    trips=[e for e in events if e['kind']=='network_relay_trip']
    starts=[e for e in events if e['kind']=='transfer_started']
    losses=[e for e in events if e['kind']=='relay_measurement_lost'];restores=[e for e in events if e['kind']=='relay_measurement_restored']
    blocked=[e for e in events if e['kind']=='transfer_blocked']
    closes=[e for e in events if e['kind']=='breaker_close']
    if ats:
        if trips or losses or restores or len(starts)!=int(scenario!='ats_recovered_dip'):raise ValueError('transfer detection inventory differs')
        if starts:
            start=starts[0]['time_s']
            if abs(start-.06)>2e-10:raise ValueError('transfer detection delay differs')
            req=next(e for e in events if e['kind']=='breaker_open_requested')
            op=next(e for e in events if e['kind']=='breaker_open')
            if req['time_s']!=start or req['cause']!='transfer:ats' or abs(req['due_s']-.065)>2e-10 or op['time_s']!=req['due_s']:raise ValueError('primary transfer actuation differs')
        expected_reason='primary_not_open' if scenario=='ats_primary_failure' else 'reserve_unavailable' if scenario=='ats_reserve_low' else None
        if len(blocked)!=int(expected_reason is not None) or (blocked and (blocked[0]['reason']!=expected_reason or abs(blocked[0]['time_s']-.075)>2e-10)):raise ValueError('transfer blocking reason/dead time differs')
        expected_close=scenario in ('ats_transfer','ats_sync_blocked')
        if len(closes)!=int(expected_close):raise ValueError('reserve close inventory differs')
        if closes:
            c=closes[0]
            if c['cause']!='transfer:ats' or abs(c['time_s']-.08)>2e-10 or c['outcome']!=('applied' if scenario=='ats_transfer' else 'blocked'):raise ValueError('reserve closing differs')
            if c['synchronism']['reason']!=('synchronized' if scenario=='ats_transfer' else 'frequency_difference'):raise ValueError('reserve synchronism differs')
        if scenario=='ats_recovered_dip' and any(e['kind'].startswith('breaker_') for e in events):raise ValueError('short dip caused actuation')
    else:
        if starts or blocked or len(losses)!=1 or len(restores)!=int(scenario!='ct_never_restore'):raise ValueError('measurement command inventory differs')
        for e in losses+restores:
            if e['equipment_id']!='primary-oc' or e['outcome']!='applied' or e['measurement_available']!=(e['kind']=='relay_measurement_restored'):raise ValueError('measurement availability metadata differs')
        if any(losses[0]['time_s']<=e['time_s']<(restores[0]['time_s'] if restores else .5) for e in trips):raise ValueError('invalid measurement caused a trip')
        if len(trips)!=(0 if scenario=='ct_never_restore' else 1 if scenario=='pt_interrupt' else 2):raise ValueError('post-restoration trip inventory differs')
        if scenario=='ct_reclose' and (len(closes)!=1 or closes[0]['outcome']!='applied'):raise ValueError('CT outage reclose differs')
    return dict(measurement_losses=len(losses),measurement_restores=len(restores),relay_trips=len(trips),transfer_starts=len(starts),transfer_blocks=len(blocked),reserve_closes=sum(e['outcome']=='applied' and e['equipment_id']=='reserve-cb' for e in closes),reserve_close_blocks=sum(e['outcome']=='blocked' and e['equipment_id']=='reserve-cb' for e in closes))


def producers():
    from benchmarks.protected_topology_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/measurement_transfer_cases.py','benchmarks/measurement_transfer_reference.py',
              'benchmarks/measurement_transfer_acceptance.py'):
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
    refs = [MeasurementTransferReference(job['study']).run(max_step=.002),
            MeasurementTransferReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
    from benchmarks.measurement_transfer_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==22 and all(r['passed'] for r in rows),physical_cases=22,
        native_runs=88,independent_trajectories=44,comparisons=176,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored R(T) CT/PT loss-restoration with reset memory and reclose; separate passport-temperature standby GENCLS source-to-generator transfer, short-dip cancellation, primary failure, reserve-voltage and synchronism blocking. No joint-heat standby initialization, multi-island, CT fault substitution, generator thermal reclose, OEM or arbitrary root ties.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)

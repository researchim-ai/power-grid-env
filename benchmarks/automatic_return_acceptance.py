"""Independent qualification of opt-in automatic recovery and pending-action cancellation."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.automatic_return_reference import AutomaticReturnReference
from power_grid_env.study_artifacts import encoded, seal, digest


def compact_comparisons(checks):
    """Keep all gates/errors; reference packets already retain full waveforms."""
    return [{k:v for k,v in check.items() if k!='values'} for check in checks]


def event_key(e):
    check=e.get('synchronism',{})
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value','outcome','cause','breaker_id','threshold','measurement_available','measurement_side','generation','cancelled_actions'))+(check.get('allowed'),check.get('reason'),e.get('reason'))


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
    starts=[e for e in events if e['kind']=='automatic_return_started'];completed=[e for e in events if e['kind']=='automatic_return_completed'];blocked=[e for e in events if e['kind']=='automatic_return_blocked']
    expected_starts=2 if scenario in ('loss_before_open','loss_after_open','inflight_drop','cancel_reset') else 1
    expected_complete=int(scenario in ('success','dwell_dip','pt_dwell','loss_before_open','cancel_reset'))
    if len(starts)!=expected_starts or len(completed)!=expected_complete:raise ValueError('automatic return cycle inventory differs')
    cancelled=[e for e in events if e['kind']=='return_cancel']
    if len(cancelled)!=int(scenario in ('cancel_reset','cancel_after_open')):raise ValueError('cancel command differs')
    if cancelled and cancelled[0]['cancelled_actions']!=(2 if scenario=='cancel_reset' else 1):raise ValueError('pending action cancellation differs')
    if scenario in ('dwell_dip','pt_dwell'):
        changes=[e for e in events if e['kind']=='return_recovery_cancelled']
        expected='primary_measurement_unavailable' if scenario=='pt_dwell' else 'primary_voltage_unavailable'
        if len(changes)!=1 or changes[0]['reason']!=expected or abs(starts[0]['time_s']-.19)>2e-10:raise ValueError('healthy dwell was not restarted fully')
    if scenario=='success' and (abs(starts[0]['time_s']-.16)>2e-10 or abs(completed[0]['time_s']-.18)>2e-10):raise ValueError('normal recovery timing differs')
    for start in starts:
        requests=[e for e in events if e['kind']=='breaker_open_requested' and e.get('cause')=='automatic_return:ats:'+str(start['generation'])]
        if len(requests)!=1 or requests[0]['time_s']!=start['time_s'] or abs(requests[0]['due_s']-start['time_s']-.005)>1e-12:raise ValueError('return reserve-opening request differs')
    if scenario=='cancel_reset' and abs(completed[0]['time_s']-.23)>2e-10:raise ValueError('cancel/reset fresh dwell differs')
    if scenario=='loss_before_open' and (len(blocked)!=1 or blocked[0]['reason']!='primary_voltage_unavailable' or abs(completed[0]['time_s']-.25)>2e-10):raise ValueError('pre-opening failure/reset differs')
    fallback=[e for e in events if e['kind']=='automatic_return_fallback_armed']
    if len(fallback)!=int(scenario in ('loss_after_open','inflight_drop')):raise ValueError('fallback inventory differs')
    if fallback and len([e for e in events if e['kind']=='transfer_started'])!=2:raise ValueError('forward ATS not rearmed by return failure')
    if scenario=='reserve_fail_open' and (len(blocked)!=1 or blocked[0]['reason']!='reserve_not_open'):raise ValueError('failed reserve opening allowed grid closing')
    if scenario in ('loss_after_open','inflight_drop') and [e['reason'] for e in blocked]!=['primary_voltage_unavailable','close_angle_difference']:raise ValueError('fallback and final synchronism blocking differ')
    return dict(return_starts=len(starts),return_completions=len(completed),return_blocks=len(blocked),fallbacks=len(fallback),cancelled_actions=sum(e['cancelled_actions'] for e in cancelled),recovery_resets=sum(e['kind']=='return_recovery_cancelled' for e in events),forward_transfers=sum(e['kind']=='transfer_started' for e in events))


def producers():
    from benchmarks.transfer_lifecycle_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/automatic_return_cases.py','benchmarks/automatic_return_reference.py',
              'benchmarks/automatic_return_acceptance.py'):
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
    refs = [AutomaticReturnReference(job['study']).run(max_step=.002),
            AutomaticReturnReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
    from benchmarks.automatic_return_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==22 and all(r['passed'] for r in rows),physical_cases=22,
        native_runs=88,independent_trajectories=44,comparisons=176,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored passport-temperature warm-standby R(T) ATS with opt-in automatic return, uninterrupted hysteretic recovery dwell, guarded reserve opening and primary closing, forward fallback, actuator failure and explicit pending-return cancellation/reset. No multi-island, source frequency events, primary-close failure fallback, OEM or arbitrary hybrid-root certification.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)

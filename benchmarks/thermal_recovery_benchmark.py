"""Independent repeated thermal trip/reset/reconnection qualification."""
import copy
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from benchmarks.thermal_recovery_cases import jobs
from benchmarks.thermal_recovery_reference import ThermalRecoveryReference
from benchmarks.standby_interlocks_benchmark import report_sample, producers as inherited
from benchmarks.automatic_return_acceptance import compare as base_compare, reference_packet, compact_comparisons
from benchmarks.automatic_return_verify import clock_gate
from benchmarks.energy_integral_gate import native_error, reference_error
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, digest, seal, verify
from power_grid_env.model import canonical_hash

SCOPE='Authored 380V/50Hz two-node warm-standby GENCLS/two-cage R(T), explicit stator-I2 reset and retrip; passport/hot temperatures, 0.3s. Test dead/live threshold .5pu is not an operating recommendation. No OEM, field-thermal reset, arbitrary root ties or multi-island qualification.'


def producers():
    result=inherited()
    for name in ('cases','reference','benchmark'):
        p=Path('benchmarks/thermal_recovery_'+name+'.py');result[str(p)]=digest(p.read_bytes())
    return result


def compare(ref,report):
    result=base_compare(ref,report)
    if result['passed']:
        result['reset_levels_match']=all(abs(a['level']-b.get('level',float('inf')))<=2e-9 for a,b in zip(ref.events,report['event_log']) if a['kind']=='generator_thermal_reset')
        result['passed']=result['passed'] and result['reset_levels_match']
    return result


def coverage(events,scenario):
    trips=[e for e in events if e['kind']=='generator_thermal_trip']
    resets=[e for e in events if e['kind']=='generator_thermal_reset']
    closes=[e for e in events if e['kind']=='breaker_close' and e.get('outcome') in ('applied','blocked')]
    blocked=[e for e in closes if e['outcome']=='blocked']
    failure=scenario in ('hot_reset','connected_reset')
    if len(trips)!=(1 if failure else 2) or len(resets)!=(2 if scenario=='double_reset' else 1):raise ValueError('trip/reset inventory differs')
    expected='blocked' if failure else 'applied'
    if resets[0]['outcome']!=expected:raise ValueError('reset outcome differs')
    if failure and resets[0].get('reason')!=('generator_connected' if scenario=='connected_reset' else 'thermal_level_above_reset'):raise ValueError('reset block reason differs')
    if scenario=='double_reset' and resets[1]['outcome']!='already_in_requested_state':raise ValueError('duplicate reset changed state')
    reason={'hot_reset':'generator_thermal_latch','connected_reset':'generator_thermal_latch',
            'manual_retrip':'angle_difference','minimum_open':'minimum_open_time','operator_inhibit':'manual_inhibit'}.get(scenario)
    if [e.get('reason',e.get('synchronism',{}).get('reason')) for e in blocked]!=([reason] if reason else []):raise ValueError('close block inventory differs')
    if not failure and not .22<trips[-1]['time_s']<.247:raise ValueError('retrip not reached')
    if scenario=='close_deadline':
        batch=[e['kind'] for e in events if abs(e['time_s']-.22)<1e-12]
        if batch.index('generator_thermal_reset')>batch.index('breaker_close'):raise ValueError('command-before-queue ordering differs')
    return dict(thermal_trips=len(trips),reset_applied=sum(e['outcome']=='applied' for e in resets),
                reset_blocked=sum(e['outcome']=='blocked' for e in resets),reset_noop=sum(e['outcome']=='already_in_requested_state' for e in resets),
                close_applied=sum(e['outcome']=='applied' for e in closes),close_blocked=len(blocked),
                transfer_starts=sum(e['kind']=='transfer_started' for e in events))


def physical_gate(sample,events,scenario):
    for t in sorted({e['time_s'] for e in events}):
        a,b=[sample(t,side) for side in ('pre_event','post_event')]
        for k in ('g.delta','g.omega','g.heat.temperature_c','g.stator_thermal_level','m.speed_pu','m.e_real_pu','m.e_imag_pu','m.stator_temperature_c','m.rotor_temperature_c'):
            if a[k]!=b[k]:raise ValueError('physical state jump: '+k)
    for i in range(151):
        t=i*.002;q=sample(t);latch=0
        for e in events:
            if e['time_s']>t:continue
            if e['kind']=='generator_thermal_trip':latch=1
            if e['kind']=='generator_thermal_reset' and e['outcome']=='applied':latch=0
        if q['g.stator_thermal_latched']!=latch or q['source.connected']+q['g.connected']>1:raise ValueError('latch/topology differs')
        trips=[e for e in events if e['kind']=='generator_thermal_trip' and e['time_s']<=t]
        if trips and not q['g.connected']:
            last=trips[-1];expected=last['level']*np.exp(-(t-last['time_s'])/.02)
            if abs(q['g.stator_thermal_level']-expected)>2e-9:raise ValueError('cooling memory changed')
    final=sample(.3)
    if final['g.connected']!=0 or final['g.stator_thermal_latched']!=1:raise ValueError('final lockout differs')
    if scenario=='operator_inhibit' and (sample(.23)['reserve-cb.manual_inhibit']!=1 or final['reserve-cb.manual_inhibit']!=0):raise ValueError('reset erased operator isolation')


def inspect(job,root,produce):
    refs=[ThermalRecoveryReference(job['study']).run(max_step=.002),ThermalRecoveryReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    if produce:root.mkdir(parents=True,exist_ok=False)
    else:verify(root)
    maximum_reference=maximum_native=maximum_clock=0.;segments=0;totals={}
    for name,r in zip(('reference','refined-reference'),refs):
        coverage(r.events,job['scenario']);physical_gate(r.sample,r.events,job['scenario'])
        maximum_reference=max(maximum_reference,reference_error(r));n,error=clock_gate(r);segments+=n;maximum_clock=max(maximum_clock,error)
        packet=reference_packet(r)
        if produce:(root/(name+'.json')).write_bytes(encoded(packet))
        elif encoded(packet)!=encoded(read_json(root/(name+'.json'))):raise ValueError('independent replay differs')
    # Native trajectories must pass against BOTH reference refinements.
    for method in ('radau','network_dae'):
        for grid in (0,1):
            spec=copy.deepcopy(job['study']);spec['coupled'].update(integration_method=method,step_s=.002/2**grid);owner=StudySpec.model_validate(spec)
            leaf=root/(method+'-'+str(grid))
            if produce:
                report=run_study(owner);leaf.mkdir()
            else:
                verify(leaf);report=read_json(leaf/'trace.json')
                if read_json(leaf/'study.json')!=owner.model_dump(mode='json'):raise ValueError('owner differs')
            if not report.get('completed') or not report['additional_validation']['ok']:raise ValueError('incomplete native study')
            if report['settings']!=owner.coupled.model_dump(mode='json') or report['network_sha256']!=canonical_hash(owner.network) or report['code_sha256']!=package_fingerprint():raise ValueError('native identity differs')
            checks=compact_comparisons([compare(r,report) for r in refs])
            if not all(c['passed'] for c in checks):raise ValueError('reference comparison failed: '+job['id'])
            physical_gate(report_sample(report),report['event_log'],job['scenario'])
            maximum_native=max(maximum_native,native_error(report))
            for k,v in coverage(report['event_log'],job['scenario']).items():totals[k]=totals.get(k,0)+v
            if produce:
                for name,data in [('study',owner.model_dump(mode='json')),('trace',report),('comparison',checks)]:(leaf/(name+'.json')).write_bytes(encoded(data))
                seal(leaf)
            elif encoded(checks)!=encoded(read_json(leaf/'comparison.json')):raise ValueError('stored comparison differs')
    result=dict(id=job['id'],passed=True,actions=totals,reference_clock_segments=segments,maximum_clock_error=maximum_clock,
                maximum_native_energy_error_j=maximum_native,maximum_reference_energy_error_j=maximum_reference)
    if produce:(root/'summary.json').write_bytes(encoded(result));seal(root);print(job['id'],'passed',flush=True)
    elif encoded(result)!=encoded(read_json(root/'summary.json')):raise ValueError('case audit differs')
    return result


def execute(job,root):return inspect(job,root,True)


def run(out,source=None):
    fp=package_fingerprint();hashes=producers();declared=list(jobs());target=Path(out)
    if source is None:
        target.mkdir(parents=True,exist_ok=False)
        with ProcessPoolExecutor(max_workers=2) as pool:rows=list(pool.map(execute,declared,[target/j['id'] for j in declared]))
    else:
        source=Path(source);anchor=verify(source);s=read_json(source/'summary.json')
        if not s['passed'] or s['code_sha256']!=fp or s['producers']!=hashes or [r['id'] for r in s['rows']]!=[j['id'] for j in declared]:raise ValueError('source inventory/runtime differs')
        rows=[inspect(j,source/j['id'],False) for j in declared]
        for scenario in ('close_deadline','operator_inhibit'):
            for mode in ('passport','hot'):
                for method in ('radau','network_dae'):
                    for grid in (0,1):
                        a,b=[read_json(source/(mode+'-'+scenario+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ('','-reversed')]
                        if any(a[k]!=b[k] for k in ('time_s','sample_side','event_log','channels')):raise ValueError('permutation changed trace')
        if verify(source)!=anchor:raise ValueError('source changed')
        target.mkdir(parents=True,exist_ok=False)
    totals={k:sum(r['actions'][k] for r in rows) for k in rows[0]['actions']}
    expected=dict(thermal_trips=144,reset_applied=64,reset_blocked=16,reset_noop=8,close_applied=144,close_blocked=48,transfer_starts=104)
    if len(rows)!=20 or totals!=expected or fp!=package_fingerprint() or hashes!=producers():raise ValueError('aggregate coverage/producers changed')
    result=dict(passed=True,code_sha256=fp,producers=hashes,physical_cases=20,native_runs=80,independent_trajectories=40,comparisons=160,
                actions=totals,reference_clock_segments=sum(r['reference_clock_segments'] for r in rows),
                maximum_clock_error=max(r['maximum_clock_error'] for r in rows),
                maximum_native_energy_error_j=max(r['maximum_native_energy_error_j'] for r in rows),
                maximum_reference_energy_error_j=max(r['maximum_reference_energy_error_j'] for r in rows),rows=rows,scope=SCOPE,independent_host=False)
    if source is not None:result.update(source_manifest_sha256=anchor,permutation_pairs=16)
    (target/'summary.json').write_bytes(encoded(result));seal(target);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source');p.add_argument('--out',required=True)
    print({k:v for k,v in run(**vars(p.parse_args())).items() if k not in ('rows','producers')})

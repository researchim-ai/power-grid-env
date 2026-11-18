"""Sealed initial-observation/thermal-latch qualification and separate consumer.

All acceptance gates are declared before qualification. Reference replay is
performed again by verify; the installed consumer has no benchmark imports.
"""
import copy
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np

from benchmarks.standby_interlocks_cases import jobs
from benchmarks.standby_interlocks_reference import StandbyInterlocksReference
from benchmarks.automatic_return_acceptance import (
    compare, event_key, reference_packet, compact_comparisons, producers as inherited_producers)
from benchmarks.automatic_return_verify import clock_gate
from benchmarks.energy_integral_gate import native_error, reference_error
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import read_json, encoded, digest, seal, verify


SCOPE = ('Authored two-node 380V/50Hz warm-standby GENCLS/two-cage R(T) profile; '
         'initial PT unavailability, restoration/retry, persistent I2 latch with '
         'cooling and blocked manual/ATS reconnection. Prescribed temperatures, '
         '0.3s horizon. No OEM, primary close-failure fallback, several live '
         'islands, field-thermal reconnection or arbitrary simultaneous root claims.')


def producers():
    result = inherited_producers()
    for p in Path('benchmarks').glob('standby_interlocks*.py'):
        result[str(p)] = digest(p.read_bytes())
    result['benchmarks/automatic_return_verify.py'] = digest(Path('benchmarks/automatic_return_verify.py').read_bytes())
    result['benchmarks/energy_integral_gate.py'] = digest(Path('benchmarks/energy_integral_gate.py').read_bytes())
    return result


def coverage(events, scenario, mode='passport'):
    trips = [e for e in events if e['kind'] == 'generator_thermal_trip']
    closes = [e for e in events if e['kind'] == 'breaker_close' and 'synchronism' in e]
    thermal_blocks = [e for e in closes if e['synchronism']['reason'] == 'generator_thermal_latch']
    starts = [e for e in events if e['kind'] == 'transfer_started']
    blocked = [e for e in events if e['kind'] == 'transfer_blocked']
    completed = [e for e in events if e['kind'] == 'automatic_return_completed']
    expected_starts = 0 if scenario == 'primary_missing' else 3 if scenario == 'thermal_retry' else 2 if scenario == 'reserve_retry' else 1
    if (len(trips) != int(scenario.startswith('thermal_')) or
        len(thermal_blocks) != (2 if scenario.startswith('thermal_') else 0) or
        len(starts) != expected_starts or
        len(completed) != int(scenario == 'reserve_restore' or (scenario == 'both_restore' and mode == 'passport')) or
        len(blocked) != int(scenario in ('reserve_retry', 'reserve_missing'))):
        raise ValueError('interlock scenario event inventory differs: '+scenario)
    if trips and not .08 < trips[0]['time_s'] < .081:
        raise ValueError('thermal trip outside declared crossing interval')
    expected_first = .08 if scenario == 'primary_restore' else .07 if scenario == 'both_restore' else .06
    if starts and abs(starts[0]['time_s']-expected_first) > 2e-10:
        raise ValueError('initial PT detection timing differs')
    expected_reason = {'primary_restore': 'voltage_difference', 'reserve_retry': 'frequency_difference'}
    if scenario in expected_reason and closes[-1]['synchronism']['reason'] != expected_reason[scenario]:
        raise ValueError('restoration/retry bypassed synchrocheck')
    if scenario == 'both_restore' and mode == 'hot' and closes[-1]['synchronism']['reason'] != 'voltage_difference':
        raise ValueError('hot initial restoration bypassed voltage window')
    return dict(thermal_trips=len(trips), thermal_close_blocks=len(thermal_blocks),
                transfer_starts=len(starts), transfer_blocks=len(blocked),
                return_completions=len(completed))


def report_sample(report):
    def sample(t, side='regular'):
        eps = 64*np.finfo(float).eps*max(1., abs(t))
        indices = [i for i, (time, s) in enumerate(zip(report['time_s'], report['sample_side']))
                   if abs(time-t) <= eps and s == side]
        if not indices and side == 'regular':
            indices = [i for i, (time, s) in enumerate(zip(report['time_s'], report['sample_side']))
                       if abs(time-t) <= eps and s in ('initial', 'post_event')]
        if len(indices) != 1:
            raise ValueError('missing/ambiguous native sample')
        return {k: v[indices[0]] for k, v in report['channels'].items()}
    return sample


def physical_gate(sample, events, scenario):
    for t in sorted({e['time_s'] for e in events}):
        pre, post = [sample(t, side) for side in ('pre_event', 'post_event')]
        for k in ('g.delta', 'g.omega', 'g.heat.temperature_c', 'g.stator_thermal_level',
                  'm.speed_pu', 'm.e_real_pu', 'm.e_imag_pu',
                  'm.stator_temperature_c', 'm.rotor_temperature_c'):
            if pre[k] != post[k]:
                raise ValueError('physical jump: '+k)
    thermal = scenario.startswith('thermal_')
    trip = next((e for e in events if e['kind'] == 'generator_thermal_trip'), None)
    for i in range(151):
        t = i*.002
        q = sample(t)
        if q['source.connected']+q['g.connected'] > 1:
            raise ValueError('parallel sources')
        if q['g.stator_thermal_latched'] != int(trip is not None and t >= trip['time_s']):
            raise ValueError('thermal latch lost or fabricated')
        if trip and t > trip['time_s']:
            expected = trip['level']*np.exp(-(t-trip['time_s'])/.02)
            if q['g.connected'] != 0 or abs(q['g.stator_thermal_level']-expected) > 2e-9:
                raise ValueError('latched generator reconnected or zero-current cooling differs')
        if abs(q['g.stator.copper_electrical_loss_w']-q['g.heat.copper_loss_w']) > 1e-7:
            raise ValueError('electrical/heat loss partition differs')
    initial = sample(0.)
    if not thermal:
        primary = scenario not in ('primary_restore', 'both_restore', 'primary_missing')
        reserve = scenario not in ('reserve_restore', 'reserve_retry', 'both_restore', 'reserve_missing')
        if initial['ats.primary_measurement_valid'] != int(primary) or initial['ats.reserve_measurement_valid'] != int(reserve):
            raise ValueError('initial measurement masks differ')
        for side, valid in (('primary', primary), ('reserve', reserve)):
            if not valid and initial['ats.'+side+'_voltage_pu'] != 0:
                raise ValueError('unavailable observation did not report zero')
        if initial['g.bus_voltage_pu'] < .5:
            raise ValueError('observation mask changed physical initial voltage')
    final = sample(.3)
    if thermal:
        if final['g.stator_thermal_level'] >= .03 or final['g.stator_thermal_latched'] != 1:
            raise ValueError('latch not retained after cooling below trip')
        if final['reserve-cb.close_attempts'] != 3:
            raise ValueError('manual/ATS close attempts differ')
    if scenario == 'primary_missing' and (final['grid-cb.open_attempts'] != 0 or final['ats.progress'] != 0):
        raise ValueError('invalid initial primary PT fabricated detection')
    if scenario == 'reserve_missing' and final['reserve-cb.close_attempts'] != 0:
        raise ValueError('invalid reserve PT allowed close request')


def execute(job, root):
    root.mkdir(parents=True, exist_ok=False)
    refs = [StandbyInterlocksReference(job['study']).run(max_step=.002),
            StandbyInterlocksReference(job['study']).run(max_step=.001, rtol=5e-12, atol=5e-13)]
    for ref in refs:
        coverage(ref.events, job['scenario'], job['mode'])
        physical_gate(ref.sample, ref.events, job['scenario'])
    rows = []
    for method in ('radau', 'network_dae'):
        for grid in (0, 1):
            owner = copy.deepcopy(job['study'])
            owner['coupled'].update(integration_method=method, step_s=.002/2**grid)
            spec = StudySpec.model_validate(owner)
            report = run_study(spec)
            if not report.get('completed'):
                raise ValueError('native integration incomplete: '+str(report.get('reason')))
            checks = compact_comparisons([compare(r, report) for r in refs])
            if not report['additional_validation']['ok'] or not all(c['passed'] for c in checks):
                raise ValueError('native/reference comparison failed: '+job['id'])
            physical_gate(report_sample(report), report['event_log'], job['scenario'])
            effects = coverage(report['event_log'], job['scenario'], job['mode'])
            leaf = root/(method+'-'+str(grid))
            leaf.mkdir()
            for name, data in [('study', spec.model_dump(mode='json')), ('trace', report), ('comparison', checks)]:
                (leaf/(name+'.json')).write_bytes(encoded(data))
            seal(leaf)
            rows.append(dict(method=method, grid=grid, passed=True, coverage=effects))
    for name, ref in zip(('reference', 'refined-reference'), refs):
        (root/(name+'.json')).write_bytes(encoded(reference_packet(ref)))
    result = dict(id=job['id'], passed=True, rows=rows)
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    print(job['id'], 'passed', flush=True)
    return result


def qualify(out):
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    fp, hashes = package_fingerprint(), producers()
    with ProcessPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(execute, list(jobs()), [root/j['id'] for j in jobs()]))
    if len(rows) != 20 or fp != package_fingerprint() or hashes != producers():
        raise ValueError('qualification inventory or producers changed')
    result = dict(passed=True, physical_cases=20, native_runs=80, independent_trajectories=40,
                  comparisons=160, code_sha256=fp, producers=hashes, rows=rows, scope=SCOPE)
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    return result


def accept(source, out):
    root = Path(source)
    anchor, fp = verify(root), package_fingerprint()
    summary = read_json(root/'summary.json')
    declared = list(jobs())
    if not summary['passed'] or summary['code_sha256'] != fp or summary['producers'] != producers() or [r['id'] for r in summary['rows']] != [j['id'] for j in declared]:
        raise ValueError('qualification owner/runtime mismatch')
    totals = dict(thermal_trips=0, thermal_close_blocks=0, transfer_starts=0,
                  transfer_blocks=0, return_completions=0)
    max_native = max_reference = max_clock = 0.
    clocks = 0
    for job in declared:
        child = root/job['id']
        verify(child)
        refs = [StandbyInterlocksReference(job['study']).run(max_step=.002),
                StandbyInterlocksReference(job['study']).run(max_step=.001, rtol=5e-12, atol=5e-13)]
        for name, ref in zip(('reference', 'refined-reference'), refs):
            if encoded(reference_packet(ref)) != encoded(read_json(child/(name+'.json'))):
                raise ValueError('independent snapshot replay differs')
            physical_gate(ref.sample, ref.events, job['scenario'])
            coverage(ref.events, job['scenario'], job['mode'])
            n, error = clock_gate(ref)
            clocks += n
            max_clock = max(max_clock, error)
            max_reference = max(max_reference, reference_error(ref))
        if [event_key(e) for e in refs[0].events] != [event_key(e) for e in refs[1].events] or any(abs(a['time_s']-b['time_s']) > 1e-8 for a, b in zip(refs[0].events, refs[1].events)):
            raise ValueError('reference event refinement differs')
        for point in reference_packet(refs[0])['regular_values']:
            t = point['time_s']
            if any(abs(t-e['time_s']) < 2e-7 for e in refs[1].events):
                continue
            a, b = [r.sample(t) for r in refs]
            for k in a:
                limit = 1e-4 if k.endswith(('_w', '_var', '_j', '.measurement', '.current_a')) else 1e-7
                if abs(a[k]-b[k]) > limit:
                    raise ValueError('reference signal refinement differs: '+k)
        for method in ('radau', 'network_dae'):
            for grid in (0, 1):
                leaf = child/(method+'-'+str(grid))
                verify(leaf)
                owner = copy.deepcopy(job['study'])
                owner['coupled'].update(integration_method=method, step_s=.002/2**grid)
                spec = StudySpec.model_validate(owner)
                report = read_json(leaf/'trace.json')
                if (read_json(leaf/'study.json') != spec.model_dump(mode='json') or
                    report['settings'] != spec.coupled.model_dump(mode='json') or
                    report['network_sha256'] != canonical_hash(spec.network) or report['code_sha256'] != fp):
                    raise ValueError('native owner/runtime mismatch')
                checks = compact_comparisons([compare(r, report) for r in refs])
                if not report['completed'] or not report['additional_validation']['ok'] or not all(c['passed'] for c in checks) or encoded(checks) != encoded(read_json(leaf/'comparison.json')):
                    raise ValueError('native comparison differs')
                physical_gate(report_sample(report), report['event_log'], job['scenario'])
                for k, v in coverage(report['event_log'], job['scenario'], job['mode']).items():
                    totals[k] += v
                max_native = max(max_native, native_error(report))
                audit = report['operating_point']
                if audit['initial_voltage_constraints']['g']['kind'] != 'standby_internal_voltage_target' or any(abs(audit['connected_machine_spectral_abscissa_per_s']-r.initial['energy_spectral_abscissa_per_s']) > 2e-6 for r in refs):
                    raise ValueError('initial audit/spectrum differs')
    for scenario in ('both_restore', 'thermal_retry'):
        for mode in ('passport', 'hot'):
            for method in ('radau', 'network_dae'):
                for grid in (0, 1):
                    a, b = [read_json(root/(mode+'-'+scenario+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ('', '-reversed')]
                    if any(a[k] != b[k] for k in ('time_s', 'sample_side', 'event_log', 'channels')):
                        raise ValueError('equipment/event permutation differs')
    if verify(root) != anchor or package_fingerprint() != fp or summary['producers'] != producers():
        raise ValueError('evidence changed')
    target = Path(out)
    target.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=fp, source_manifest_sha256=anchor,
                  independent_reference_replays=40, recomputed_comparisons=160,
                  native_energy_integrals=80, permutation_pairs=16, native_actions=totals,
                  reference_clock_segments=clocks, maximum_clock_error=max_clock,
                  maximum_native_energy_error_j=max_native, maximum_reference_energy_error_j=max_reference,
                  independent_host=False, scope=SCOPE)
    (target/'summary.json').write_bytes(encoded(result))
    seal(target)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['qualify', 'verify'])
    p.add_argument('--source')
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = qualify(args.out) if args.action == 'qualify' else accept(args.source, args.out)
    print({k: v for k, v in result.items() if k not in ('rows', 'producers')})

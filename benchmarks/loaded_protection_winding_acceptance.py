"""Bind independent hybrid winding trajectories to sealed loaded protection runs."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import math
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from benchmarks.running_fault_cartesian import CartesianProtocol

from benchmarks.loaded_protection_winding_reference import ProtectionReference
from benchmarks.motor_only_loaded_protection import jobs
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint

EVENT_TOLERANCE_S = 2e-7


def event_key(event):
    return (event['kind'], event['equipment_id'], event.get('outcome') if event['kind'] in ('breaker_open', 'motor_open', 'thermal_trip') else None)


def compare(reference, report):
    """Integrate first; align only matched one-sided event samples afterwards.

    Regular samples use absolute time. No interpolation across an event and
    no removal of failed comparisons. Event timing has its own fixed gate.
    """
    times, sides = report['time_s'], report['sample_side']
    if not report.get('completed') or not times or len(times) != len(sides) or any(not math.isfinite(t) for t in times) or any(a > b for a, b in zip(times, times[1:])):
        raise ValueError('invalid native completion/sample axis')
    if abs(times[0]) > 1e-12 or abs(times[-1]-reference.w.s.duration_s) > 1e-10:
        raise ValueError('native trajectory does not cover reference horizon')
    if any(len(v) != len(times) or any(not math.isfinite(x) for x in v) for v in report['channels'].values()):
        raise ValueError('nonfinite or truncated native channels')
    if any(side not in ('initial', 'regular', 'pre_event', 'post_event') for side in sides):
        raise ValueError('unknown native sample side')
    if any(not math.isfinite(e['time_s']) or not 0 <= e['time_s'] <= reference.w.s.duration_s for e in report['event_log']):
        raise ValueError('invalid native event time')
    expected = reference.events
    actual = report['event_log']
    identities = [event_key(e) for e in expected] == [event_key(e) for e in actual]
    event_error = max((abs(a['time_s']-b['time_s']) for a, b in zip(expected, actual)), default=0.) if identities else None
    if not identities or event_error > EVENT_TOLERANCE_S:
        return dict(passed=False, event_identities_match=identities, maximum_event_error_s=event_error,
                    event_tolerance_s=EVENT_TOLERANCE_S, expected_events=expected, actual_events=actual)
    aligned = []
    for a, b in zip(actual, expected):
        pair = (a['time_s'], b['time_s'])
        if pair not in aligned:
            aligned.append(pair)
    errors = {}
    limits = {}
    missing = set()
    rows = []
    aligned_count = 0
    for i, (t, side) in enumerate(zip(report['time_s'], report['sample_side'])):
        sample_t = t
        if side in ('pre_event', 'post_event'):
            pair = next((p for p in aligned if abs(t-p[0]) < 1e-11), None)
            if pair:
                sample_t = pair[1]
                aligned_count += 1
        values = reference.sample(sample_t, side)
        rows.append(dict(time_s=t, reference_time_s=sample_t, sample_side=side, channels=values))
        for key, value in values.items():
            if key not in report['channels']:
                missing.add(key)
                continue
            error = abs(value-report['channels'][key][i])
            errors[key] = max(errors.get(key, 0.), error)
            limits[key] = .001 if key.endswith(('.current_a', '.measurement')) else (5e-6 if key.endswith(('.current_pu', '.thermal_level', '.progress')) else 2e-6)
    return dict(passed=not missing and all(errors[k] <= limits[k] for k in errors),
                missing_channels=sorted(missing), event_identities_match=True, maximum_event_error_s=event_error,
                event_tolerance_s=EVENT_TOLERANCE_S, errors=errors, limits=limits, samples=len(rows),
                aligned_one_sided_samples=aligned_count, values=rows)


def execute(args):
    job, source, destination = args
    source, destination = Path(source), Path(destination)
    anchors = {}
    studies = []
    reports = []
    for method in ('radau', 'network_dae'):
        name = job['id'].replace('-'+job['method']+'-', '-'+method+'-')
        packet = source/name
        anchors[name] = verify(packet)
        study = read_json(packet/'study.json')
        if study != StudySpec.model_validate(dict(job['study'], coupled=dict(job['study']['coupled'], integration_method=method))).model_dump(mode='json'):
            raise ValueError('source study differs from owner recipe')
        if verify(packet) != next(r['manifest_sha256'] for r in read_json(source/'index/summary.json')['rows'] if r['id'] == name):
            raise ValueError('native packet not bound to source index')
        report = read_json(packet/'trace.json')
        spec = StudySpec.model_validate(study)
        if report.get('settings') != spec.coupled.model_dump(mode='json') or report.get('network_sha256') != canonical_hash(spec.network):
            raise ValueError('native settings/network identity mismatch')
        if report['code_sha256'] != package_fingerprint() or not report['completed']:
            raise ValueError('native runtime fingerprint/completion mismatch')
        studies.append(study)
        reports.append(report)
    reference = ProtectionReference(studies[0]).run(max_step=.002)
    refined = ProtectionReference(studies[0]).run(max_step=.001, rtol=5e-12, atol=5e-13)
    checks = [compare(reference, report) for report in reports]
    refined_checks = [compare(refined, report) for report in reports]
    # Refinement is directly checked, independently of agreement with native.
    ref_event_error = None
    ref_state_error = None
    if [event_key(e) for e in reference.events] == [event_key(e) for e in refined.events]:
        ref_event_error = max(abs(a['time_s']-b['time_s']) for a, b in zip(reference.events, refined.events))
        # Fixed physical times, excluding event neighborhoods; event jumps are
        # checked through the independently aligned comparisons above.
        times = [i*.001 for i in range(601) if all(abs(i*.001-e['time_s']) > EVENT_TOLERANCE_S for e in reference.events+refined.events)]
        ref_state_error = max(abs(v-refined.sample(t)[k]) for t in times for k, v in reference.sample(t).items())
    passed = all(c['passed'] for c in checks+refined_checks) and ref_event_error is not None and ref_event_error < 1e-8 and ref_state_error < 1e-5
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in [('study', studies[0]), ('events', reference.events), ('refined-events', refined.events),
                        ('initialization', reference.initial), ('comparisons', checks), ('refined-comparisons', refined_checks)]:
        (destination/(name+'.json')).write_bytes(encoded(value))
    summary = dict(id=job['id'], passed=passed, source_manifests=anchors, independent_trajectories=2,
                   native_comparisons=4, refinement_event_error_s=ref_event_error,
                   refinement_channel_max_abs_error=ref_state_error,
                   comparisons=[{k: v for k, v in c.items() if k != 'values'} for c in checks])
    (destination/'summary.json').write_bytes(encoded(summary))
    seal(destination)
    for name, anchor in anchors.items():
        if verify(source/name) != anchor:
            raise ValueError('native packet changed during qualification')
    return dict(**summary, manifest_sha256=verify(destination))


def qualify(protocol, source, out, workers=4):
    if not 1 <= workers <= 16:
        raise ValueError('workers must be1..16')
    root, source = Path(out), Path(source)
    source_anchor = verify(source/'index')
    stored = read_json(source/'index/summary.json')
    if not stored['passed'] or stored['code_sha256'] != package_fingerprint() or stored['producer_sha256'] != digest(Path('benchmarks/motor_only_loaded_protection.py').read_bytes()):
        raise ValueError('source producer/runtime/outcome differs')
    if CartesianProtocol.model_validate(read_json(source/'index/protocol.json')) != CartesianProtocol.model_validate(protocol):
        raise ValueError('owner protocol differs from source')
    expected_ids = {j['id'] for j in jobs(protocol)}
    if len(stored['rows']) != 192 or {r['id'] for r in stored['rows']} != expected_ids:
        raise ValueError('source index coverage differs')
    root.mkdir(parents=True, exist_ok=False)
    rows = []
    selected = [j for j in jobs(protocol) if j['method'] == 'network_dae' and j['grid'] == 2]
    if len(selected) != 32:
        raise ValueError('expected32 distinct physical scenarios')
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(execute, (j, str(source), str(root/j['id']))) for j in selected]):
            row = future.result()
            rows.append(row)
            print('independent loaded protection', len(rows), '/32', row['passed'], flush=True)
    if verify(source/'index') != source_anchor:
        raise ValueError('source index changed')
    result = dict(passed=all(r['passed'] for r in rows), physical_scenarios=32,
                  independent_trajectories=64, distinct_native_comparisons=64, comparisons_including_refinement=128,
                  source_index_sha256=source_anchor, code_sha256=package_fingerprint(),
                  producers={p: digest(Path(p).read_bytes()) for p in [__file__, 'benchmarks/loaded_protection_winding_reference.py', 'benchmarks/motor_only_loaded_reference.py']},
                  rows=sorted(rows, key=lambda r: r['id']),
                  scope='Independent T-root, SI winding/KCL DOP853, OC clock roots, CT loss, primary/failed breaker, backup and local UV opening;4 network endpoints,2 heat policies,matched no-UV controls;VI curves in this corpus. Independent refinement. No OEM/normative/continuous uncertainty or global-model superiority claim.')
    (root/'summary.json').write_bytes(encoded(result))
    (root/'protocol.json').write_bytes(encoded(protocol))
    # Nested manifests bind packets; the root seal also binds all packet files.
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol', required=True)
    p.add_argument('--source', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    r = qualify(read_json(a.protocol), a.source, a.out, a.workers)
    print(dict(passed=r['passed'], physical_scenarios=r['physical_scenarios']))
    if not r['passed']:
        raise SystemExit(2)

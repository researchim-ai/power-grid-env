"""Preregistered three-step online ANDES motor-control acceptance corpus.

Native commands are computed online, before reference event logs are inspected.
Only matched event-side samples use the corresponding native event time;
regular samples keep the original clock. This qualifies a custom benchmark
driver on a declared circuit subset, never stock ANDES controls or field data.
"""
from benchmarks.event_sampling import native_sample_sides,native_sample_indices
from pathlib import Path
import argparse
import json
import math

import numpy as np

from benchmarks.automatic_motor_group import NAMES, example, signature, compare as si_compare
from benchmarks.andes_motor_reference import LIMITS
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.native_closed_loop import native_reference, plant_case
from benchmarks.native_terminal_measurements import reconstruct_terminals
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json, sha256_file
from power_grid_env.study_spec import StudySpec

ROOT = Path(__file__).resolve().parents[1]
STEPS = (.00025, .000125, .0000625)
CASES = (*NAMES, 'grid_cold')
EVENT_LIMIT_S = 1e-6


def source_identity():
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted((ROOT/'benchmarks').rglob('*.py'))}


def load_case(name):
    if name not in CASES:
        raise ValueError('unknown online corpus case')
    if name == 'grid_cold':
        return StudySpec.model_validate_json((ROOT/'cases/studies/automatic_group_grid_cold.json').read_text())
    return example(name)


def channel_limits(spec):
    s = spec.coupled if spec.coupled else spec.induction
    limits = {m.id+'.'+k: v for m in s.motors for k, v in LIMITS.items()}
    for m in s.motors:
        limits[m.id+'.connected'] = 0.
        if m.thermal:
            limits[m.id+'.thermal_level'] = 1e-4
            limits[m.id+'.thermal_latched'] = 0.
        if m.contactor:
            for k in ('contactor_voltage_available', 'contactor_pending_restart', 'contactor_restart_count', 'contactor_timer_active'):
                limits[m.id+'.'+k] = 0.
            limits[m.id+'.contactor_timer_remaining_s'] = 1e-6
            limits[m.id+'.contactor_sensed_voltage_pu'] = 1e-4
    for g in spec.network.generators:
        for k, v in dict(delta=1e-4, omega=1e-5, e1q=1e-3, e1d=1e-3, e2d=1e-3, e2q=1e-3, p_w=1000., q_var=1000.).items():
            limits[g.id+'.'+k] = v
        limits[g.id+'.TGOV1.valve_pu'] = 1e-4
        limits[g.id+'.SEXS.field_pu'] = 1e-3
    return limits


def match_events(expected, observed):
    wanted = [e for e in expected if signature(e) is not None]
    got = [e for e in observed if signature(e) is not None]
    if [signature(e) for e in wanted] != [signature(e) for e in got]:
        raise ValueError('online native event decisions/order mismatch')
    times = [float(e['time_s']) for e in wanted+got]
    if not all(math.isfinite(t) and t >= 0 for t in times):
        raise ValueError('invalid online event time')
    return max((abs(a['time_s']-b['time_s']) for a, b in zip(wanted, got)), default=0.)


def align_event_sides(reference, native):
    """Pair groups by order, preserving even sub-nanosecond distinct events."""
    clocks = []
    for report in (reference, native):
        t = np.asarray(report['time_s'], dtype=float)
        sides = report['sample_side']
        if len(t) != len(sides) or len(t) < 2 or not np.isfinite(t).all() or np.any(np.diff(t) < 0):
            raise ValueError('invalid event comparison timeline')
        groups = []
        semantic_sides=native_sample_sides(report)
        for i, side in enumerate(sides):
            if side == 'pre_event':
                if i+1 >= len(t) or sides[i+1] != 'post_event' or t[i+1] != t[i]:
                    raise ValueError('unpaired event sides')
                if semantic_sides[i]=='pre_event':groups.append(float(t[i]))
            elif side == 'post_event':
                if i == 0 or sides[i-1] != 'pre_event' or t[i-1] != t[i]:
                    raise ValueError('unpaired event sides')
            elif side == 'initial':
                if i != 0 or t[i] != 0:
                    raise ValueError('invalid initial sample')
            elif side != 'regular':
                raise ValueError('unknown sample side')
        if any(a >= b for a, b in zip(groups, groups[1:])):
            raise ValueError('duplicated event group')
        clocks.append(groups)
    if len(clocks[0]) != len(clocks[1]):
        raise ValueError('online native event group count mismatch')
    if abs(reference['time_s'][0]-native['time_s'][0]) > 1e-12 or abs(reference['time_s'][-1]-native['time_s'][-1]) > 1e-12:
        raise ValueError('online comparison horizon mismatch')
    mapping = dict(zip(*clocks))
    aligned = [mapping.get(t,t) if side in ('pre_event', 'post_event') else t for t, side in zip(reference['time_s'], reference['sample_side'])]
    if np.any(np.diff(aligned) < 0):
        raise ValueError('event alignment crosses a regular sample')
    return aligned, max((abs(a-b) for a, b in zip(*clocks)), default=0.)


def normalized_error(errors, limits):
    if set(errors) != set(limits) or not limits:
        raise ValueError('online comparison channel inventory mismatch')
    if any(not math.isfinite(v) or v < 0 for v in (*errors.values(), *limits.values())):
        raise ValueError('nonfinite or negative error/limit')
    return max((errors[k]/v if v else (0. if errors[k] == 0 else math.inf)) for k, v in limits.items())


def qualify(rows):
    if [r['step_s'] for r in rows] != list(STEPS):
        raise ValueError('complete ordered three-step refinement required')
    if any(not r.get('completed') for r in rows):
        return dict(passed=False, reason='incomplete_native_run')
    if any(not math.isfinite(r['maximum_event_time_error_s']) or r['maximum_event_time_error_s'] < 0 for r in rows):
        raise ValueError('invalid event time error')
    if any(r['limits'] != rows[0]['limits'] for r in rows):
        raise ValueError('refinement limits changed')
    scores = [max(normalized_error(r['errors'], r['limits']), r['maximum_event_time_error_s']/EVENT_LIMIT_S) for r in rows]
    # Both production and independent SI speed/angle comparisons must improve;
    # errors already below 1e-10 are treated as numerical equilibrium noise.
    keys = [k for k in rows[0]['errors'] if k.endswith(('.speed_pu', '.delta'))]
    if not keys or any(set(r['errors']) != set(rows[0]['errors']) for r in rows):
        raise ValueError('refinement channel inventory changed')
    convergence = all(rows[-1]['errors'][k] < rows[0]['errors'][k] or rows[-1]['errors'][k] <= 1e-10 for k in keys)
    finest = scores[-1] <= 1.
    return dict(passed=bool(finest and convergence), finest_within_limits=finest, convergence_passed=convergence,
                maximum_normalized_errors_by_step=[v if math.isfinite(v) else None for v in scores])


def compare_trace(spec, native, production, independent):
    if not native.get('completed'):
        return dict(completed=False, step_s=native.get('step_s'), failure=native)
    limits = channel_limits(spec)
    if set(native['channels']) != set(limits):
        raise ValueError('unexpected online native channels')
    event_delta = max(match_events(production['event_log'], native['native_driver_metadata']['event_log']),
                      match_events(independent['independent_event_log'], native['native_driver_metadata']['event_log']))
    times, side_delta = align_event_sides(production, native)
    selected=native_sample_indices(production,(spec.coupled or spec.induction).step_s)
    semantic_sides=native_sample_sides(production)
    times=[times[i] for i in selected];comparison_sides=[semantic_sides[i] for i in selected]
    errors = {}; bounds = {}
    for key, bound in limits.items():
        measured = sample_native(native, times, comparison_sides, key)
        for label, ref in (('production', production), ('SI', independent)):
            if key not in ref['channels']:
                # The winding SI oracle does not export electromagnetic torque.
                if label == 'SI' and key.endswith('.te_pu'):
                    continue
                raise ValueError('missing '+label+' comparison channel: '+key)
            values = np.asarray(ref['channels'][key], dtype=float)[selected]
            if values.shape != measured.shape or not np.isfinite(values).all():
                raise ValueError('invalid reference channel: '+key)
            errors[label+':'+key] = float(np.max(abs(measured-values)))
            bounds[label+':'+key] = bound
    observer = reconstruct_terminals(plant_case(spec), native, additional_event_times={e['time_s'] for e in native['native_driver_metadata']['event_log']})
    for suffix in ('p_w', 'q_var'):
        key = spec.network.sources[0].id+'.'+suffix
        trace = dict(time_s=native['time_s'], sample_side=native['sample_side'], channels={key: observer['channels']['source.'+suffix]})
        measured = sample_native(trace, times, comparison_sides, key)
        for label, ref in (('production', production), ('SI', independent)):
            if key in ref['channels']:
                errors[label+':'+key] = float(np.max(abs(measured-np.asarray(ref['channels'][key])[selected])))
                bounds[label+':'+key] = 1000.
    residual = native['maximum_accepted_algebraic_residual_pu']
    if not math.isfinite(residual) or residual > 1e-8:
        raise ValueError('online native algebraic residual exceeded')
    return dict(completed=True, step_s=native['step_s'], errors=errors, limits=bounds,
                maximum_event_time_error_s=max(event_delta, side_delta), events_match=True,
                maximum_algebraic_residual_pu=residual, root_trials=native['native_driver_metadata']['root_trial_steps'],
                rollback_count=native['native_driver_metadata']['rollback_count'], regular_sample_clock_unchanged=True,
                comparison_sampling='export_grid_and_physical_event_sides',
                continuous_subgrid_root_samples_omitted=len(production['time_s'])-len(selected))


def run(out, names=CASES):
    out = Path(out)
    if not names or len(set(names)) != len(names) or any(name not in CASES for name in names):
        raise ValueError('unique registered case names required')
    if (out/'protocol.json').exists():
        raise ValueError('use a new output directory to preserve previous evidence')
    sources = source_identity(); code = package_fingerprint()
    protocol = dict(schema='power-grid-env.native-online/v1', cases=list(names), native_steps_s=list(STEPS),
                    case_sha256={n:canonical_hash(load_case(n)) for n in names}, code_sha256=code, source_sha256=sources,
                    channel_limits={n:channel_limits(load_case(n)) for n in names}, event_time_limit_s=EVENT_LIMIT_S,
                    refinement='Every fine speed/angle error improves vs coarse or is <=1e-10; every fine channel meets limits',
                    sampling='Only paired event sides change clocks; regular sample times stay unchanged',
                    stock_native_controller_qualified=False, industrial_profile_qualified=False, field_validated=False)
    write_json(out/'protocol.json', protocol)
    rows = []
    write_json(out/'summary.json', dict(passed=False, status='running', cases=rows))
    try:
        for name in names:
            spec = load_case(name); folder = out/name
            if canonical_hash(spec) != protocol['case_sha256'][name]:
                raise ValueError('registered case changed during corpus: '+name)
            write_json(folder/'study.json', spec.model_dump(mode='json'))
            # Native driver sees only the authored study; no oracle event logs.
            natives = []
            for h in STEPS:
                print('native', name, h, flush=True)
                native = native_reference(spec, h)
                write_json(folder/f'andes-{h}.json', native); natives.append(native)
            si = si_compare(spec, folder/'reference')
            if not si['passed']:
                raise ValueError('fresh production/SI reference mismatch: '+name)
            production = json.loads((folder/'reference/production.json').read_text())
            independent = json.loads((folder/'reference/independent.json').read_text())
            steps = []
            for h, native in zip(STEPS, natives):
                row = compare_trace(spec, native, production, independent); row['step_s'] = h
                steps.append(row)
            result = dict(case_id=name, steps=steps, **qualify(steps))
            write_json(folder/'comparison.json', result); rows.append(result)
            print('compared', name, result['passed'], flush=True)
            if (source_identity() != sources or package_fingerprint() != code
                    or any(canonical_hash(load_case(n)) != h for n, h in protocol['case_sha256'].items())):
                raise ValueError('producer sources changed during corpus')
            write_json(out/'summary.json', dict(passed=False, status='running', cases=rows))
        result = dict(passed=all(r['passed'] for r in rows), status='complete', cases=rows,
                      full_registered_corpus=list(names)==list(CASES), new_native_runs=3*len(rows),
                      protocol_sha256=sha256_file(out/'protocol.json'), code_sha256=code, source_sha256=sources,
                      controller_commands_precomputed=False, stock_native_controller_qualified=False,
                      field_validated=False, industrial_profile_qualified=False)
        result['artifacts_sha256'] = {str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p != out/'summary.json'}
        write_json(out/'summary.json', result)
        return result
    except Exception as exc:
        write_json(out/'summary.json', dict(passed=False, status='failed', cases=rows, error=str(exc)))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    args = parser.parse_args()
    if not run(args.out, args.cases)['passed']:
        raise SystemExit(1)

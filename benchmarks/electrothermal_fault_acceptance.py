"""Independent warm-winding R(T)/fault qualification with matched fixed-R controls."""
import copy
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from benchmarks.electrothermal_fault_cases import jobs
from benchmarks.electrothermal_fault_reference import run_reference
from power_grid_env.phase_research import PhaseStudy, evaluate
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest

# Fixed numerical agreement gates; not equipment-accuracy guarantees.
CAPS = dict(voltage_v=3e-4, current_a=3e-4, current_alpha_a=3e-4, current_beta_a=3e-4,
            motor_speed_rad_s=3e-5, torque_nm=3e-5, stator_temperature_c=3e-6, rotor_temperature_c=3e-6,
            stator_resistance_ohm=3e-7, rotor1_resistance_ohm=3e-7, rotor2_resistance_ohm=3e-7, rotor3_resistance_ohm=3e-7,
            rotor_copper_w=.003, stator_copper_w=.003, core_loss_w=.003, stator_flux_alpha_vs=3e-7,
            stator_flux_beta_vs=3e-7, stator_net_heat_w=.003, rotor_net_heat_w=.003, ambient_heat_w=.0001, cross_heat_w=.0001)
PRODUCERS = ['benchmarks/electrothermal_fault_cases.py', 'benchmarks/electrothermal_fault_reference.py',
             'benchmarks/electrothermal_fault_acceptance.py', 'benchmarks/phase_core_acceptance.py',
             'cases/studies/flux-saturated_dol.json']


def producers():
    return {p: digest(Path(p).read_bytes()) for p in PRODUCERS}


def comparison(doc, report, reference):
    if not report['completed'] or len(report['time_s']) != len(reference['records']) or report['time_s'] != reference['time_s'] or report['sample_side'] != reference['sample_side']:
        raise ValueError('incomplete/misaligned independent phase trajectory')
    if reference['primary_rhs_used']:
        raise ValueError('reference must not call native RHS')
    errors, limits = {}, {}
    for key in reference['records'][0]['channels']:
        a = np.asarray(report['channels'][key])
        b = np.array([r['channels'][key] for r in reference['records']])
        if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
            raise ValueError('nonfinite electrothermal trajectory')
        errors[key] = float(max(abs(a-b)))
        limits[key] = CAPS[key.split('.')[-1]]+1e-6*float(max(abs(b)))
    scale = max(1., max(abs(e['entered_j']) for e in report['energy']), max(abs(e['loss_j']) for e in report['energy']))
    energy = max(abs(e['residual_j']) for e in report['energy'])/scale
    ref_energy = max(abs(r['energy_error_j']) for r in reference['records'])/scale
    return dict(passed=all(errors[k] <= limits[k] for k in errors) and energy < 1e-5 and ref_energy < 1e-6 and report['kcl_max_a'] < 1e-7,
                errors=errors, limits=limits, native_energy_relative=energy, reference_energy_relative=ref_energy, kcl_max_a=report['kcl_max_a'])


def thermal_audit(doc, report):
    """Independent algebraic heat-flow/R(T) identities from saved measurements."""
    m = doc['case']['motors'][0]
    p, ch = m['passport'], report['channels']
    h = p['thermal']
    prefix = m['id']+'.'
    ts, tr = (np.array(ch[prefix+k+'_temperature_c']) for k in ['stator', 'rotor'])
    errors = []
    errors.append(max(abs(np.array(ch[prefix+'stator_resistance_ohm'])-p['stator_r_ohm']*(1+h['alpha_per_c']*(ts-h['reference_c'])))))
    for i, cage in enumerate(p['cages']):
        errors.append(max(abs(np.array(ch[prefix+f'rotor{i+1}_resistance_ohm'])-cage['resistance_ohm']*(1+h['alpha_per_c']*(tr-h['reference_c'])))))
    hs = h['stator_ambient_w_per_k']*(ts-h['ambient_c'])
    hr = h['rotor_ambient_w_per_k']*(tr-h['ambient_c'])
    cross = h['cross_w_per_k']*(ts-tr)
    expected = dict(stator_net_heat_w=np.array(ch[prefix+'stator_copper_w'])+np.array(ch[prefix+'core_loss_w'])-hs-cross,
                    rotor_net_heat_w=np.array(ch[prefix+'rotor_copper_w'])-hr+cross, ambient_heat_w=hs+hr, cross_heat_w=cross)
    heat_errors = {k: float(max(abs(np.array(ch[prefix+k])-v))) for k, v in expected.items()}
    initial = m.get('initial_temperature_c', [h['ambient_c']]*2)
    return dict(passed=bool(max(errors) < 1e-10 and max(heat_errors.values()) < 1e-8 and ts[0] == initial[0] and tr[0] == initial[1] and max(max(ts), max(tr)) < h['maximum_c']),
                resistance_identity_error_ohm=float(max(errors)), heat_identity_errors_w=heat_errors,
                final_stator_c=float(ts[-1]), final_rotor_c=float(tr[-1]), initial_temperature_c=initial)


def execute(args):
    job, out = args
    doc = job['study']
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    native = []
    rows = []
    for method in ['Radau', 'DOP853']:
        for grid, step in enumerate([.0002, .0001]):
            owner = copy.deepcopy(doc)
            owner['emt'].update(method=method, max_step_s=step)
            result = evaluate(PhaseStudy.model_validate(owner))
            native.append(result)
            name = method+'-'+str(grid)
            child = root/name
            child.mkdir()
            for key, value in [('study', owner), ('trace', result)]:
                (child/(key+'.json')).write_bytes(encoded(value))
            seal(child)
    reference = run_reference(doc['case'], native[0]['time_s'], max_step=5e-5, sides=native[0]['sample_side'])
    refined = run_reference(doc['case'], native[0]['time_s'], max_step=2.5e-5, sides=native[0]['sample_side'])
    for ref in [reference, refined]:
        ref.update(time_s=native[0]['time_s'], sample_side=native[0]['sample_side'])
    # Direct reference refinement, without conditioning on a native state.
    deltas = {k: max(abs(a['channels'][k]-b['channels'][k]) for a, b in zip(reference['records'], refined['records'], strict=True)) for k in reference['records'][0]['channels']}
    refined_ok = all(v < .05*CAPS[k.split('.')[-1]] for k, v in deltas.items())
    for index, result in enumerate(native):
        checks = [comparison(doc, result, r) for r in [reference, refined]]
        thermal = thermal_audit(doc, result)
        rows.append(dict(method=['Radau', 'DOP853'][index//2], grid=index%2, passed=thermal['passed'] and all(c['passed'] for c in checks),
                         comparisons=checks, thermal_audit=thermal, rhs_evaluations=result['rhs_evaluations']))
    row = dict(**{k: v for k, v in job.items() if k != 'study'}, passed=refined_ok and all(r['passed'] for r in rows),
               checks=rows, reference_refinement_errors=deltas)
    for name, value in [('reference', reference), ('refined-reference', refined), ('summary', row)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root)
    return dict(**row, manifest_sha256=verify(root))


def qualify(out, workers=4):
    if not 1 <= workers <= 16:
        raise ValueError('workers must be1..16')
    fingerprint, source_hashes = package_fingerprint(), producers()
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    declared = list(jobs())
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(execute, (j, str(root/j['id']))) for j in declared]):
            row = future.result()
            rows.append(row)
            print('electrothermal fault', len(rows), '/24', row['passed'], flush=True)
    effects = []
    for job in declared:
        if not job['feedback']:
            continue
        other = next(j for j in declared if not j['feedback'] and all(j[k] == job[k] for k in ['cages', 'start', 'fault']))
        a, b = (read_json(root/j['id']/'Radau-1/trace.json') for j in [job, other])
        owner_a, owner_b = copy.deepcopy(job['study']), copy.deepcopy(other['study'])
        # Only experiment IDs, alpha and its explicit annotation can differ.
        for owner in [owner_a, owner_b]:
            owner['id'] = owner['case']['id'] = 'matched'
            owner['case']['motors'][0]['passport']['thermal']['alpha_per_c'] = 0.
        effects.append(dict(id=job['id'], matched_inputs=owner_a == owner_b,
                            current_vector_max_difference_a=float(max(np.hypot(np.array(a['channels']['core.current_alpha_a'])-b['channels']['core.current_alpha_a'], np.array(a['channels']['core.current_beta_a'])-b['channels']['core.current_beta_a']))),
                            final_speed_difference_rad_s=a['channels']['core.motor_speed_rad_s'][-1]-b['channels']['core.motor_speed_rad_s'][-1],
                            final_stator_temperature_difference_c=a['channels']['core.stator_temperature_c'][-1]-b['channels']['core.stator_temperature_c'][-1]))
    result = dict(passed=len(rows) == 24 and all(r['passed'] for r in rows) and all(e['matched_inputs'] and e['current_vector_max_difference_a'] > 1e-5 for e in effects),
                  physical_scenarios=24, native_runs=96, independent_trajectories=48, comparisons=192,
                  code_sha256=fingerprint, producers=source_hashes, rows=sorted(rows, key=lambda r: r['id']), matched_feedback_effects=effects,
                  scope='Authored single/three-cage saturated core-loss full-winding RLC feeder, cold start/hot rotating initial state, LL/3ph/repeated LL faults; matched alpha=0 controls. Dynamic R(T), temperatures, heat balances and separate all-flux KCL/DOP853 reference. Radau/DOP853,two max steps and independent refinement. No loaded steady-state initialization, RMS electrothermal extension, OEM accuracy, trip/selectivity or continuous uncertainty certificate.')
    if fingerprint != package_fingerprint() or source_hashes != producers():
        raise ValueError('producer changed during qualification')
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=4)
    result = qualify(**vars(p.parse_args()))
    print(dict(passed=result['passed'], native_runs=result['native_runs']))
    if not result['passed']:
        raise SystemExit(2)

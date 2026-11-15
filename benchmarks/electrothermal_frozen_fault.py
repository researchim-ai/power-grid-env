"""Isolate dynamic feedback: freeze Rs/Rr at the SAME hot initial resistances.

The main alpha=0 corpus uses reference-temperature resistances. For hot
machines it includes both static-temperature and dynamic-feedback effects.
This additional six-case control removes that ambiguity explicitly.
"""
import copy
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from benchmarks.electrothermal_fault_cases import jobs
from benchmarks.electrothermal_fault_acceptance import execute, producers
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest


def freeze_initial(study):
    owner = copy.deepcopy(study)
    m = owner['case']['motors'][0]
    p, h = m['passport'], m['passport']['thermal']
    ts, tr = m.get('initial_temperature_c', [h['ambient_c']]*2)
    p['stator_r_ohm'] *= 1+h['alpha_per_c']*(ts-h['reference_c'])
    for cage in p['cages']:
        cage['resistance_ohm'] *= 1+h['alpha_per_c']*(tr-h['reference_c'])
    h['alpha_per_c'] = 0.
    owner['id'] += '-frozen-initial-R'
    owner['case']['id'] = owner['id']
    return owner


def qualify(source, out, workers=4):
    if not 1 <= workers <= 16:
        raise ValueError('workers must be1..16')
    source, root = Path(source), Path(out)
    anchor = verify(source)
    original = read_json(source/'summary.json')
    if not original['passed'] or original['code_sha256'] != package_fingerprint() or original['producers'] != producers():
        raise ValueError('original qualification/producer mismatch')
    root.mkdir(parents=True, exist_ok=False)
    selected = [j for j in jobs() if j['feedback'] and j['start'] == 'hot_rotating']
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {}
        for j in selected:
            control = dict(j, id=j['id']+'-frozen-initial-R', feedback=False, study=freeze_initial(j['study']))
            pending[pool.submit(execute, (control, str(root/control['id'])))] = j
        for future in as_completed(pending):
            row = future.result()
            j = pending[future]
            # Both native engines, both meshes; all initial physical channels
            # and all input fields apart from the declared R law match.
            effects = []
            for method in ['Radau', 'DOP853']:
                for grid in [0, 1]:
                    directory = method+'-'+str(grid)
                    a = read_json(source/j['id']/directory/'trace.json')
                    b = read_json(root/row['id']/directory/'trace.json')
                    control_owner = read_json(root/row['id']/directory/'study.json')
                    expected = freeze_initial(j['study'])
                    expected['emt'].update(method=method, max_step_s=.0002 if grid == 0 else .0001)
                    same_input = control_owner == expected
                    initial_fields = ['current_alpha_a', 'current_beta_a', 'motor_speed_rad_s', 'stator_temperature_c', 'rotor_temperature_c',
                                      'stator_resistance_ohm']+[f'rotor{i+1}_resistance_ohm' for i in range(j['cages'])]
                    initial_error = max(abs(a['channels']['core.'+k][0]-b['channels']['core.'+k][0]) for k in initial_fields)
                    delta = np.hypot(np.array(a['channels']['core.current_alpha_a'])-b['channels']['core.current_alpha_a'],
                                     np.array(a['channels']['core.current_beta_a'])-b['channels']['core.current_beta_a'])
                    frozen_rs = b['channels']['core.stator_resistance_ohm']
                    effects.append(dict(method=method, grid=grid, owner_matches=same_input, initial_channel_error=float(initial_error),
                                        maximum_dynamic_current_difference_a=float(max(delta)),
                                        frozen_resistance_constant=max(frozen_rs) == min(frozen_rs),
                                        final_rotor_temperature_difference_c=a['channels']['core.rotor_temperature_c'][-1]-b['channels']['core.rotor_temperature_c'][-1]))
            row['dynamic_effects'] = effects
            row['dynamic_feedback_isolated'] = all(e['owner_matches'] and e['initial_channel_error'] < 1e-10 and e['frozen_resistance_constant'] and e['maximum_dynamic_current_difference_a'] > 1e-5 for e in effects)
            rows.append(row)
            print('frozen-initial-R', len(rows), '/6', row['passed'], row['dynamic_feedback_isolated'], flush=True)
    result = dict(passed=len(rows) == 6 and all(r['passed'] and r['dynamic_feedback_isolated'] for r in rows),
                  native_runs=24, independent_trajectories=12, source_manifest_sha256=anchor,
                  source_path=str(source), code_sha256=package_fingerprint(),
                  producer_sha256=digest(Path(__file__).read_bytes()), base_producers=producers(), rows=sorted(rows, key=lambda r: r['id']),
                  scope='Hot initial Rs/Rr, fluxes,speed,temperatures,network,fault schedule and heat equations are matched. Only subsequent R(T) feedback is frozen.6 selected hot controls,single/three cages,LL/3ph/repeated faults,two engines/two steps,two independent references each. No OEM accuracy or RMS electrothermal qualification.')
    if verify(source) != anchor:
        raise ValueError('original source changed during frozen control')
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=4)
    result = qualify(**vars(p.parse_args()))
    print(dict(passed=result['passed'], native_runs=result['native_runs']))
    if not result['passed']:
        raise SystemExit(2)

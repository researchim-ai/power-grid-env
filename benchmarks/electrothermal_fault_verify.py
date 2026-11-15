"""Recompute owner bindings, numerical gates and independent references offline."""
import copy
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmarks.electrothermal_fault_cases import jobs
from benchmarks.electrothermal_fault_acceptance import comparison, thermal_audit, producers, CAPS
from benchmarks.electrothermal_fault_reference import run_reference
from benchmarks.electrothermal_frozen_fault import freeze_initial
from power_grid_env.phase_research import PhaseStudy
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, verify, seal, digest


def verify_packet(args):
    job, path, replay = args
    path = Path(path)
    anchor = verify(path)
    row = read_json(path/'summary.json')
    if any(row[k] != job[k] for k in ['id', 'cages', 'start', 'fault', 'feedback']):
        raise ValueError('owner case identity differs')
    refs = [read_json(path/(n+'.json')) for n in ['reference', 'refined-reference']]
    if replay:
        for ref, step in zip(refs, [5e-5, 2.5e-5]):
            fresh = run_reference(job['study']['case'], ref['time_s'], max_step=step, sides=ref['sample_side'])
            if len(fresh['records']) != len(ref['records']):
                raise ValueError('reference replay inventory differs')
            for a, b in zip(fresh['records'], ref['records'], strict=True):
                if a['channels'].keys() != b['channels'].keys() or any(abs(a['channels'][k]-b['channels'][k]) > 1e-9*(1+abs(a['channels'][k])) for k in a['channels']) or abs(a['energy_error_j']-b['energy_error_j']) > 1e-7:
                    raise ValueError('independent trajectory replay differs')
    deltas = {k: max(abs(a['channels'][k]-b['channels'][k]) for a, b in zip(refs[0]['records'], refs[1]['records'], strict=True)) for k in refs[0]['records'][0]['channels']}
    if deltas != row['reference_refinement_errors'] or any(v >= .05*CAPS[k.split('.')[-1]] for k, v in deltas.items()):
        raise ValueError('reference refinement gate differs')
    checks = []
    for method in ['Radau', 'DOP853']:
        for grid, step in enumerate([.0002, .0001]):
            child = path/(method+'-'+str(grid))
            verify(child)
            owner = copy.deepcopy(job['study'])
            owner['emt'].update(method=method, max_step_s=step)
            if read_json(child/'study.json') != PhaseStudy.model_validate(owner).model_dump(mode='json'):
                raise ValueError('native study differs from owner')
            report = read_json(child/'trace.json')
            numeric = [comparison(owner, report, ref) for ref in refs]
            heat = thermal_audit(owner, report)
            checks.append(dict(method=method, grid=grid, passed=heat['passed'] and all(c['passed'] for c in numeric),
                               comparisons=numeric, thermal_audit=heat, rhs_evaluations=report['rhs_evaluations']))
    if checks != row['checks'] or not row['passed'] or not all(c['passed'] for c in checks):
        raise ValueError('recomputed numerical/thermal gate differs')
    if verify(path) != anchor:
        raise ValueError('packet changed during verification')
    return dict(id=job['id'], passed=True, manifest_sha256=anchor)


def verify_corpus(source, frozen, out, *, replay_reference=True, workers=4):
    if not 1 <= workers <= 16:
        raise ValueError('workers must be1..16')
    source, frozen = Path(source), Path(frozen)
    anchors = [verify(p) for p in [source, frozen]]
    main, control = (read_json(p/'summary.json') for p in [source, frozen])
    fp = package_fingerprint()
    if not main['passed'] or not control['passed'] or main['code_sha256'] != fp or control['code_sha256'] != fp or main['producers'] != producers() or control['base_producers'] != producers() or control['producer_sha256'] != digest(Path('benchmarks/electrothermal_frozen_fault.py').read_bytes()) or control['source_manifest_sha256'] != anchors[0]:
        raise ValueError('producer/source/frozen binding differs')
    declared = list(jobs())
    frozen_jobs = [dict(j, id=j['id']+'-frozen-initial-R', feedback=False, study=freeze_initial(j['study'])) for j in declared if j['feedback'] and j['start'] == 'hot_rotating']
    pending = []
    for root, summary, owner in [(source, main, declared), (frozen, control, frozen_jobs)]:
        if len(summary['rows']) != len(owner) or {r['id'] for r in summary['rows']} != {j['id'] for j in owner} or {p.name for p in root.iterdir()} != {j['id'] for j in owner}|{'summary.json', 'manifest.json'}:
            raise ValueError('corpus coverage/layout differs')
        for j in owner:
            item = next(r for r in summary['rows'] if r['id'] == j['id'])
            if verify(root/j['id']) != item['manifest_sha256']:
                raise ValueError('child not bound to index')
            stored = read_json(root/j['id']/'summary.json')
            if any(item.get(k) != v for k, v in stored.items()):
                raise ValueError('packet summary not bound to root')
            pending.append((j, str(root/j['id']), replay_reference))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(verify_packet, pending))
    # Recompute the claimed dynamic effects directly from the two traces.
    import numpy as np
    for row in control['rows']:
        if not row['dynamic_feedback_isolated']:
            raise ValueError('frozen control did not isolate feedback')
        source_id = row['id'].removesuffix('-frozen-initial-R')
        for effect in row['dynamic_effects']:
            name = effect['method']+'-'+str(effect['grid'])
            a, b = [read_json(p/'trace.json')['channels'] for p in [source/source_id/name, frozen/row['id']/name]]
            fields = ['current_alpha_a', 'current_beta_a', 'motor_speed_rad_s', 'stator_temperature_c', 'rotor_temperature_c', 'stator_resistance_ohm']+[f'rotor{i+1}_resistance_ohm' for i in range(row['cages'])]
            initial_error = max(abs(a['core.'+k][0]-b['core.'+k][0]) for k in fields)
            current_error = float(max(np.hypot(np.array(a['core.current_alpha_a'])-b['core.current_alpha_a'], np.array(a['core.current_beta_a'])-b['core.current_beta_a'])))
            temperature_error = a['core.rotor_temperature_c'][-1]-b['core.rotor_temperature_c'][-1]
            constant = max(b['core.stator_resistance_ohm']) == min(b['core.stator_resistance_ohm'])
            if not effect['owner_matches'] or initial_error != effect['initial_channel_error'] or current_error != effect['maximum_dynamic_current_difference_a'] or temperature_error != effect['final_rotor_temperature_difference_c'] or constant != effect['frozen_resistance_constant'] or initial_error >= 1e-10 or current_error <= 1e-5:
                raise ValueError('frozen effect gate differs')
    if [verify(p) for p in [source, frozen]] != anchors or fp != package_fingerprint():
        raise ValueError('evidence/runtime changed during verification')
    result = dict(passed=True, code_sha256=fp, manifests={str(p): a for p, a in zip([source, frozen], anchors)},
                  verified_packets=len(rows), native_traces=120, independent_trajectories=60,
                  independent_reference_replayed=replay_reference, rows=rows,
                  scope='Offline owner/provenance/inventory binding, numerical/thermal/refinement gates recomputed. Optional full independent DOP853 replay. Stored native traces; no native rerun, OEM or source authentication claim.')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--frozen', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--no-reference-replay', dest='replay_reference', action='store_false')
    result = verify_corpus(**vars(p.parse_args()))
    print(dict(passed=result['passed'], verified_packets=result['verified_packets']))

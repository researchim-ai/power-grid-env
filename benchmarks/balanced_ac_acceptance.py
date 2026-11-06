"""Local independent ANDES PF/metering and OPF-dispatch acceptance on cached PGLib.

No acquisition, GitHub, CI or remote runner. Native ANDES parses the original
MATPOWER independently. Its own terminal equation arrays supply flow meters.
"""
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
from pathlib import Path
import argparse

import numpy as np

from power_grid_env.ac_estimation import Measurement, estimate_state
from power_grid_env.balanced_ac import solve_power_flow, dispatch_case, branch_results
from power_grid_env.matpower_case import load_case
from power_grid_env.network_opf import solve_ac_opf
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json
from benchmarks.release_candidate import source_hashes

NAMES = ('pglib_opf_case5_pjm.m', 'pglib_opf_case14_ieee.m', 'pglib_opf_case118_ieee.m')
GATES = dict(voltage_pu=1e-6, angle_rad=1e-6, flow_pu=1e-5, estimation_voltage_pu=1e-6)


def _native(path, *, dispatch=None, dropped_branch=None):
    import andes
    from andes.io.matpower import m2mpc, mpc2system
    # Independent native parser. No project case, Ybus, PF or estimator helper.
    mpc = m2mpc(str(path))
    if dispatch is not None:
        mpc['bus'][:, 7] = dispatch['vm_pu']; mpc['bus'][:, 8] = dispatch['va_deg']
        live = [i for i, row in enumerate(mpc['gen']) if row[7] == 1]
        positions = {int(row[0]): i for i, row in enumerate(mpc['bus'])}
        for k, i in enumerate(live):
            mpc['gen'][i, 1] = dispatch['pg_mw'][k]; mpc['gen'][i, 2] = dispatch['qg_mvar'][k]
            mpc['gen'][i, 5] = dispatch['vm_pu'][positions[int(mpc['gen'][i, 0])]]
    if dropped_branch is not None:
        mpc['branch'][dropped_branch, 10] = 0
    system = andes.System(no_output=True, default_config=True, pycode_path='/tmp/pge-balanced-ac-native-pycode',
                          config_option=['PQ.pq2z=0', 'PV.pv2pq=0'])
    mpc2system(mpc, system); system.setup()
    system.PFlow.config.tol = 1e-10; system.PFlow.config.max_iter = 80
    ok = system.PFlow.run()
    if not ok:
        return dict(converged=False)
    ids = [int(x) for x in system.Bus.idx.v]; index = {k: i for i, k in enumerate(ids)}
    reference = int(next(row[0] for row in mpc['bus'] if row[1] == 3))
    theta = system.Bus.a.v-system.Bus.a.v[index[reference]]
    pg = np.zeros(len(ids)); qg = np.zeros(len(ids))
    for family in [system.PV, system.Slack]:
        for i, bus in enumerate(family.bus.v):
            if family.u.v[i]:
                pg[index[int(bus)]] += family.p.v[i]*mpc['baseMVA']
                qg[index[int(bus)]] += family.q.v[i]*mpc['baseMVA']
    pd = np.array([mpc['bus'][i, 2] for i in range(len(ids))]); qd = np.array([mpc['bus'][i, 3] for i in range(len(ids))])
    flow = np.column_stack([system.Line.a1.e, system.Line.v1.e, system.Line.a2.e, system.Line.v2.e])*mpc['baseMVA']
    return dict(converged=True, bus_ids=ids, vm_pu=system.Bus.v.v.tolist(), theta_rad=theta.tolist(),
                p_injection_mw=(pg-pd).tolist(), q_injection_mvar=(qg-qd).tolist(), terminal_pq=flow.tolist())


def _meters(reference):
    rows = []
    for i, bus in enumerate(reference['bus_ids']):
        for kind, value, sigma in [('vm_pu', reference['vm_pu'][i], .001), ('va_rad', reference['theta_rad'][i], .001),
                                   ('p_injection_mw', reference['p_injection_mw'][i], .1), ('q_injection_mvar', reference['q_injection_mvar'][i], .1)]:
            rows.append(Measurement(f'{kind}-{bus}', kind, value, sigma, bus_id=bus))
    for i, values in enumerate(reference['terminal_pq']):
        for kind, value in zip(['p_from_mw','q_from_mvar','p_to_mw','q_to_mvar'], values):
            rows.append(Measurement(f'{kind}-{i}', kind, value, .1, branch_index=i))
    return rows


def _comparison(case, result, native):
    if not native['converged'] or not result['converged']:
        return dict(passed=False, reason='PF did not converge')
    idx = {k: i for i, k in enumerate(native['bus_ids'])}
    order = [idx[b.id] for b in case.buses]
    verror = float(np.max(abs(np.array(result['vm_pu'])-np.array(native['vm_pu'])[order])))
    aerror = float(np.max(abs(np.radians(result['va_deg'])-np.array(native['theta_rad'])[order])))
    flows = np.array([[r[k] for k in ['p_from_mw','q_from_mvar','p_to_mw','q_to_mvar']] for r in result['branches']])
    ferror = float(np.max(abs(flows-np.array(native['terminal_pq'])))/case.base_mva)
    return dict(passed=verror <= GATES['voltage_pu'] and aerror <= GATES['angle_rad'] and ferror <= GATES['flow_pu'],
                voltage_error_pu=verror, angle_error_rad=aerror, flow_error_pu=ferror)


def run(out):
    out = Path(out)
    if out.exists(): raise ValueError('new acceptance directory required')
    out.mkdir(parents=True); repo = Path(__file__).resolve().parents[1]
    sources = source_hashes(repo); producer = package_fingerprint(); cases = []; errors = []
    write_json(out/'sources.json', sources)
    version = importlib.metadata.version('andes')
    if version != '2.0.0': raise ValueError('native reference requires ANDES 2.0.0')
    import andes
    package = Path(andes.__file__).parent
    native_sources = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.rglob('*.py'))}
    write_json(out/'native-sources.json', native_sources)
    for name in NAMES:
        path = repo/'data/external/raw/pglib-opf'/name
        if not path.is_file():
            errors.append('missing cached input: '+name); continue
        case = load_case(path); root = out/name; root.mkdir()
        native = _native(path); ours = solve_power_flow(case, enforce_q_limits=False, max_iterations=80)
        write_json(root/'native.json', native); write_json(root/'pf.json', ours)
        comparison = _comparison(case, ours, native)
        meters = _meters(native) if native['converged'] else []
        estimate = estimate_state(case, meters) if meters else dict(completed=False)
        write_json(root/'measurements.json', [asdict(r) for r in meters]); write_json(root/'estimate.json', estimate)
        estimate_error = float(np.max(abs(np.array(estimate['vm_pu'])-np.array(native['vm_pu'])))) if estimate.get('completed') else None
        estimate_ok = estimate_error is not None and estimate_error <= GATES['estimation_voltage_pu'] and estimate['accepted']
        # Native off-diagonal terminal RHS values supply a second, deliberately corrupted meter episode.
        noisy = list(meters)
        if noisy:
            target = next(i for i, r in enumerate(noisy) if r.kind == 'p_from_mw')
            noisy[target] = replace(noisy[target], value=noisy[target].value+100.)
            cleaned = estimate_state(case, noisy, reject_bad_data=True)
            write_json(root/'bad-data.json', cleaned)
            bad_ok = cleaned.get('accepted', False) and cleaned['removed_measurement_ids'] == [noisy[target].id]
        else: bad_ok = False
        # A deterministic subset is selected before assessing errors. Islands are retained separately.
        selected = sorted({0, len(case.branches)//2, len(case.branches)-1})
        outages = []
        for i in selected:
            trial = replace(case, branches=[replace(b, status=0) if j == i else b for j, b in enumerate(case.branches)])
            solved = solve_power_flow(trial, enforce_q_limits=False, max_iterations=80)
            if solved['status'] == 'islanded':
                outages.append(dict(branch_index=i, status='islanded', independent_numeric_comparison=False)); continue
            ref = _native(path, dropped_branch=i)
            write_json(root/f'outage-{i}-native.json', ref); write_json(root/f'outage-{i}-pf.json', solved)
            if not solved['converged'] and not ref['converged']:
                cmp = dict(passed=True, evidence='both failed to converge; this does not prove voltage collapse')
            else: cmp = _comparison(trial, solved, ref)
            outages.append(dict(branch_index=i, **cmp))
        outage_ok = all(r.get('passed', r.get('status') == 'islanded') for r in outages)
        row = dict(case=name, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), pf=comparison,
                   estimation_error_pu=estimate_error, estimation_passed=estimate_ok, bad_data_passed=bad_ok,
                   outage_cases=outages, passed=comparison['passed'] and estimate_ok and bad_ok and outage_ok)
        cases.append(row)
    # Optimized PGLib 3/5/14 dispatches are independently replayed; no global-optimum claim.
    dispatches = []
    for name in ('pglib_opf_case3_lmbd.m','pglib_opf_case5_pjm.m','pglib_opf_case14_ieee.m'):
        path = repo/'data/external/raw/pglib-opf'/name
        if not path.is_file(): errors.append('missing OPF input: '+name); continue
        case = load_case(path); optimized = solve_ac_opf(case)
        write_json(out/(name+'-opf.json'), optimized)
        if optimized['success']:
            replay = solve_power_flow(dispatch_case(case, optimized), enforce_q_limits=False)
            native = _native(path, dispatch=optimized)
            write_json(out/(name+'-dispatch-native.json'), native)
            cmp = _comparison(case, replay, native)
        else: cmp = dict(passed=False, reason='OPF infeasible')
        dispatches.append(dict(case=name, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), **cmp))
    from power_grid_env.steady_study import run_steady_study, SCHEMA
    from power_grid_env.balanced_ac import case_document
    largest = repo/'data/external/raw/pglib-opf'/NAMES[-1]
    continuation = dict(passed=False, reason='missing largest public case')
    if largest.is_file():
        case = load_case(largest)
        document = dict(schema=SCHEMA, id='public-118-branch-n1', task='n1', case=case_document(case), options=dict(enforce_q_limits=False, max_iterations=80))
        full = run_steady_study(document, out/'n1-118-full')
        part = run_steady_study(document, out/'n1-118-part', max_cases=31)
        resumed = run_steady_study(document, out/'n1-118-resumed', resume_from=out/'n1-118-part')
        continuation = dict(passed=full['completed'] and resumed['completed'] and full['journal_sha256']==resumed['journal_sha256'] and part['status']=='paused',
                            evaluated_cases=full['evaluated_cases'], status_counts=full['status_counts'],
                            secure=full['secure'], failed_or_limit_violating_cases=full['failed_or_limit_violating_cases'],
                            full_seconds=full['elapsed_s'], continuation_byte_identical=full['journal_sha256']==resumed['journal_sha256'])
        unchanged = source_hashes(repo) == sources and package_fingerprint() == producer
    result = dict(passed=not errors and unchanged and continuation['passed'] and len(cases)==3 and len(dispatches)==3 and all(r['passed'] for r in cases+dispatches),
                  code_sha256=producer, source_unchanged=unchanged, errors=errors, gates=GATES, cases=cases, dispatch_replays=dispatches, continuation=continuation,
                  native_version=version, native_policy='PQ.pq2z=0, PV.pv2pq=0; native Line uses r+1e-8 and x+1e-8',
                  field_validated=False, global_optimum_claimed=False, policy='local_only_no_GitHub_no_CI')
    write_json(out/'acceptance.json', result)
    write_json(out/'manifest.json', {p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()})
    return result


def main():
    parser = argparse.ArgumentParser(__doc__); parser.add_argument('--out', type=Path, required=True); args = parser.parse_args()
    result = run(args.out); print(json.dumps(result)); raise SystemExit(0 if result['passed'] else 2)

if __name__ == '__main__': main()

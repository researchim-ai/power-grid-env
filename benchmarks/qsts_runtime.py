"""Reproducible synthetic runtime/continuation probe, not field validation.

python -m benchmarks.qsts_runtime --days 30 --out reports/qsts-runtime
"""
from __future__ import annotations
import argparse
import math
import resource
import time
from pathlib import Path

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.report import write_json
from power_grid_env.scenarios import Scenario
from power_grid_env.study_spec import StudySpec, run_study


def make_study(name: str, days: int) -> StudySpec:
    if days < 1:
        raise ValueError('days must be positive')
    b = NetBuilder(name, name, 10., s_base_va=1e6)
    for n in ('src', 'a', 'b'):
        b.node(n)
    b.source('src', .05, .2)
    b.line_z1('src', 'a', .2+.4j, line_id='first')
    b.line_z1('a', 'b', .3+.5j, line_id='second')
    if name == 'mesh':
        b.line_z1('src', 'b', .4+.7j, line_id='tie')
    b.load('a', 100000., 20000., critical=True, load_id='a')
    b.load('b', 70000., 15000., critical=True, load_id='b')
    points = [[i*900., 1.+.2*math.sin(2*math.pi*(i % 96)/96),
               1.+.1*math.cos(2*math.pi*(i % 96)/96)] for i in range(days*96)]
    scenario = Scenario(id=name, horizon_s=days*86400., control_interval_s=900.,
                        load_profile={'a': points, 'b': points}, trace_limit=2)
    return StudySpec(id=name, study='qsts', backend='opendss', network=b.build(), scenario=scenario)


def run(out: Path, days: int) -> dict:
    reports = []
    for name in ('radial', 'mesh'):
        spec = make_study(name, days)
        started = time.perf_counter()
        full = run_study(spec, out=out/name/'full')
        seconds = time.perf_counter()-started
        part = out/name/'resumed'
        run_study(spec, out=part, max_steps=days*48)
        resumed = run_study(spec, out=part, resume=True)
        expected = sum(sum(load.p_w.values()) * sum(row[1]*.25 for row in spec.scenario.load_profile[load.id])
                       for load in spec.network.loads)
        cold = spec.network.model_copy(deep=True)
        for load in cold.loads:
            p, q = spec.scenario.load_profile[load.id][-1][1:]
            load.p_w = {phase: v*p for phase, v in load.p_w.items()}
            load.q_var = {phase: v*q for phase, v in load.q_var.items()}
        oracle = solve_network(cold)
        final = full['last_result']['voltages']
        by_key = {(v.bus_id, v.phase): v for v in oracle.voltages}
        gap = max(abs(complex(v['v_real_v'], v['v_imag_v'])-by_key[v['bus_id'], v['phase']].complex)
                  / v['v_base_ln_v'] for v in final if v['phase'] != 'N' and v['v_base_ln_v'])
        energy_gap = abs(full['integrals']['critical_demand_wh']-expected)
        passed = (full['completed'] and resumed['completed'] and full['intervals'] == days*96
                  and full['journal_sha256'] == resumed['journal_sha256']
                  and energy_gap < .001 and gap < 1e-5)
        reports.append({'case': name, 'days': days, 'step_s': 900, 'intervals': full['intervals'],
                        'full_run_s': seconds, 'demand_error_wh': energy_gap,
                        'final_voltage_error_pu': gap, 'continuation_byte_identical': full['journal_sha256'] == resumed['journal_sha256'],
                        'retained_trace_steps': full['retained_trace_steps'],
                        'retained_history_steps': full['retained_history_steps'],
                        'code_sha256': full['code_sha256'], 'study_sha256': full['study_sha256'],
                        'verdict': 'pass' if passed else 'fail'})
    report = {'verdict': 'pass' if all(r['verdict']=='pass' for r in reports) else 'fail',
              'evidence': 'synthetic_runtime_and_same_engine_consistency', 'cases': reports,
              'process_peak_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'rss_is_process_lifetime_peak': True, 'repeats': 1,
              'field_validated': False, 'two_external_feeders_qualified': False,
              'competitive_superiority_proven': False}
    write_json(out/'summary.json', report)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    result=run(args.out,args.days)
    print(result)
    raise SystemExit(0 if result['verdict']=='pass' else 1)

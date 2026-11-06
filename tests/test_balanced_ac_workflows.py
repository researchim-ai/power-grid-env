"""AC workflow checks with a separately stamped exact complex-voltage oracle."""
from dataclasses import replace
import json
import math
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.balanced_ac import case_document, case_from_document, solve_power_flow, branch_results
from power_grid_env.network_opf import Bus, Branch, GeneratorSpec, OpfCase, audit_feasibility, solve_ac_opf
from power_grid_env.steady_study import run_steady_study, SCHEMA
from power_grid_env.study_artifacts import read_json, verify


def independent_injections(case, voltage):
    """End currents from series voltage drop and terminal charging, no Ybus helper."""
    ids = {b.id: i for i, b in enumerate(case.buses)}
    current = np.zeros(len(voltage), dtype=complex)
    for b in case.branches:
        if not b.status:
            continue
        i, j = ids[b.fbus], ids[b.tbus]
        tap = (b.ratio or 1)*np.exp(1j*np.deg2rad(b.angle_deg))
        drop = (voltage[i]/tap-voltage[j])/complex(b.r, b.x)
        current[i] += (drop+1j*b.b/2*voltage[i]/tap)/np.conj(tap)
        current[j] += -drop+1j*b.b/2*voltage[j]
    current += np.array([complex(b.gs, b.bs) for b in case.buses])*voltage
    return voltage*np.conj(current)*case.base_mva


def exact_case(*, tap=True, radial=False):
    buses = [Bus(k, 3 if k == 10 else 1, 0., 0., .001 if k == 20 else 0., .002 if k == 30 else 0.,
                 1.04 if k == 10 else 1., 0., .8, 1.2) for k in (10, 20, 30)]
    branches = [Branch(10, 20, .02, .08, .02, 1000., 1.02 if tap else 0., 2. if tap else 0., 1, -90., 90.),
                Branch(20, 30, .04, .12, .01, 1000., 0., 0., 1, -90., 90.)]
    if not radial:
        branches.append(Branch(10, 30, .03, .10, .01, 1000., 0., 0., 1, -90., 90.))
    case = OpfCase('authored-exact-three-bus', 100., buses,
                   [GeneratorSpec(10, -1000., 1000., -1000., 1000., (1., 0.))], branches)
    voltage = np.array([1.04, 1.00, .98])*np.exp(1j*np.array([0., -.03, -.06]))
    s = independent_injections(case, voltage)
    case = replace(case, buses=[b if b.id == 10 else replace(b, pd=-s[i].real, qd=-s[i].imag) for i, b in enumerate(case.buses)])
    return case, voltage


def study(case, task='n1'):
    return dict(schema=SCHEMA, id='balanced-acceptance', task=task, case=case_document(case))


def test_pf_matches_independent_exact_voltage_with_tap_charging_and_shunts():
    case, v = exact_case(); result = solve_power_flow(case)
    assert result['converged'] and result['within_limits'], result
    actual = np.array(result['vm_pu'])*np.exp(1j*np.radians(result['va_deg']))
    np.testing.assert_allclose(actual, v, atol=1e-10)
    assert result['audit']['max_mismatch_pu'] < 1e-9
    assert case_from_document(case_document(case)) == case
    assert result['bus_ids'] == [10, 20, 30]


def test_native_opendss_independently_solves_the_same_plain_balanced_case():
    from opendssdirect.OpenDSSDirect import OpenDSSDirect
    case, voltage = exact_case(tap=False)
    # Remove shunts/charging here; their exact oracle is checked separately above.
    case = replace(case, buses=[replace(b, gs=0., bs=0.) for b in case.buses],
                   branches=[replace(b, b=0.) for b in case.branches])
    s = independent_injections(case, voltage)
    case = replace(case, buses=[b if i == 0 else replace(b, pd=-s[i].real, qd=-s[i].imag) for i, b in enumerate(case.buses)])
    dss = OpenDSSDirect().NewContext()
    dss.Text.Command('new circuit.b10 basekv=10 pu=1.04 phases=3 bus1=n10 r1=1e-9 x1=1e-9 r0=1e-9 x0=1e-9')
    zbase = 10.**2/case.base_mva
    for i, b in enumerate(case.branches):
        dss.Text.Command(f'new line.l{i} bus1=n{b.fbus} bus2=n{b.tbus} phases=3 r1={b.r*zbase} x1={b.x*zbase} r0={b.r*zbase} x0={b.x*zbase} c1=0 c0=0 length=1 units=km')
    for b in case.buses[1:]:
        dss.Text.Command(f'new load.l{b.id} bus1=n{b.id} phases=3 conn=wye kv=10 kw={b.pd*1000} kvar={b.qd*1000} model=1 vminpu=0.1 vmaxpu=2')
    dss.Text.Command('set tolerance=1e-12 maxiterations=200'); dss.Solution.Solve()
    assert dss.Solution.Converged()
    result = solve_power_flow(case)
    for i, bus in enumerate(case.buses):
        dss.Circuit.SetActiveBus('n'+str(bus.id))
        re, im = dss.Bus.Voltages()[:2]
        actual = complex(re, im)/(10_000/math.sqrt(3.))
        assert abs(actual-voltage[i]) < 2e-8
        assert result['vm_pu'][i] == pytest.approx(abs(actual), abs=2e-8)


def test_pv_q_limits_switch_to_pq_and_keep_generator_identity():
    case, v = exact_case(tap=False)
    case = replace(case, buses=[case.buses[0], replace(case.buses[1], type_code=2, vm=1.1), case.buses[2]],
                   generators=[case.generators[0], GeneratorSpec(20, -.01, .01, -500., 500., (1., 0.), pg=0., qg=0.)])
    free = solve_power_flow(case, enforce_q_limits=False)
    limited = solve_power_flow(case)
    assert free['converged'] and not free['audit']['generators_within_limits']
    assert limited['converged'] and limited['pv_to_pq'][0]['bus_id'] == 20
    assert abs(limited['qg_mvar'][1]) == pytest.approx(.01, abs=1e-7)
    assert limited['vm_pu'][1] < 1.1


def test_n1_keeps_islands_and_newton_failures_and_immutable_resume(tmp_path):
    case, _ = exact_case(radial=True); doc = study(case)
    parent = tmp_path/'parent'; paused = run_steady_study(doc, parent, max_cases=1)
    assert paused['status'] == 'paused' and not paused['completed']
    before = verify(parent)
    continued = run_steady_study(doc, tmp_path/'continued', resume_from=parent)
    full = run_steady_study(doc, tmp_path/'full')
    assert continued['completed'] and continued['status_counts'] == {'islanded': 2}
    assert not continued['secure'] and continued['evaluated_cases'] == 2
    assert continued['journal_sha256'] == full['journal_sha256']
    assert verify(parent) == before
    rows = [json.loads(line) for line in (tmp_path/'full/cases.jsonl').read_text().splitlines()]
    assert all(not row['result']['voltage_collapse_claimed'] for row in rows)
    failed_doc = study(case, 'power_flow'); failed_doc['options'] = dict(max_iterations=1)
    failed = run_steady_study(failed_doc, tmp_path/'failed')
    assert not failed['completed'] and failed['status'] == 'newton_not_converged'


def test_n1_cancel_budget_offline_and_changed_input(tmp_path):
    case, _ = exact_case(); doc = study(replace(case, branches=[*case.branches, replace(case.branches[0], status=0)]))
    cancel = tmp_path/'cancel'; cancel.touch()
    result = run_steady_study(doc, tmp_path/'stopped', cancel_file=cancel)
    assert result['status'] == 'cancelled' and result['evaluated_cases'] == 0
    assert result['excluded_offline_branch_indices'] == [3]
    changed = {**doc, 'id': 'different'}
    with pytest.raises(ValueError, match='differs'):
        run_steady_study(changed, tmp_path/'bad', resume_from=tmp_path/'stopped')
    assert not (tmp_path/'bad').exists()
    resumed = run_steady_study(doc, tmp_path/'resumed', resume_from=tmp_path/'stopped')
    assert resumed['completed']
    budget = run_steady_study(doc, tmp_path/'budget', time_budget_s=1e-12)
    assert budget['status'] == 'time_budget_exhausted' and not budget['completed']
    with pytest.raises(ValueError, match='immutable'):
        run_steady_study(doc, tmp_path/'resumed')


@pytest.mark.parametrize('field', ['pg', 'qg', 'vm', 'theta'])
def test_audit_rejects_nonfinite_operating_point(field):
    case, v = exact_case(); result = solve_power_flow(case)
    inputs = dict(vm=np.array(result['vm_pu']), theta=np.radians(result['va_deg']),
                  pg=np.array(result['pg_mw']), qg=np.array(result['qg_mvar']))
    inputs[field][0] = np.nan
    audit = audit_feasibility(case, **inputs)
    assert not audit['feasible'] and audit['errors']


def test_both_ends_of_every_finite_rating_are_checked_even_above_old_cutoff():
    case, v = exact_case(); result = solve_power_flow(case)
    huge = replace(case, base_mva=1e6,
                   buses=[replace(b, pd=b.pd*1e4, qd=b.qd*1e4) for b in case.buses],
                   generators=[replace(case.generators[0], pmin=-1e8, pmax=1e8, qmin=-1e8, qmax=1e8)],
                   branches=[replace(b, rate_a=100001.) for b in case.branches])
    audit = audit_feasibility(huge, abs(v), np.angle(v), np.array(result['pg_mw'])*1e4, np.array(result['qg_mvar'])*1e4)
    assert not audit['flows_within_rating'] and not audit['feasible']


@pytest.mark.parametrize('mutation', ['duplicate_bus', 'zero_z', 'no_reference', 'nan_base', 'generator_bus'])
def test_invalid_cases_fail_before_optimization(mutation):
    case, _ = exact_case()
    if mutation == 'duplicate_bus': case = replace(case, buses=[case.buses[0], case.buses[0]])
    if mutation == 'zero_z': case = replace(case, branches=[replace(case.branches[0], r=0., x=0.)])
    if mutation == 'no_reference': case = replace(case, buses=[replace(b, type_code=1) for b in case.buses])
    if mutation == 'nan_base': case = replace(case, base_mva=float('nan'))
    if mutation == 'generator_bus': case = replace(case, generators=[replace(case.generators[0], bus=999)])
    with pytest.raises(ValueError): solve_ac_opf(case)


def test_matpower_runtime_parser_and_steady_cli(tmp_path, capsys):
    from power_grid_env.matpower_case import load_case
    from power_grid_env.cli import main
    source = Path('cases/reference/balanced_ac_three_bus.m')
    assert source.is_file(), 'authored parser fixture is absent'
    case = load_case(source); assert len(case.buses) == 3
    out = tmp_path/'case.json'
    assert main(['steady-study', 'import-matpower', '--source', str(source), '--out', str(out)]) == 0
    parsed = case_from_document(read_json(out)); assert parsed == case
    doc = study(case, 'opf'); input_ = tmp_path/'study.json'; input_.write_text(json.dumps(doc))
    assert main(['steady-study', 'run', '--study', str(input_), '--out', str(tmp_path/'opf')]) == 0
    result = read_json(tmp_path/'opf/result.json'); assert result['success'] and result['audit']['feasible']
    assert main(['steady-study', 'verify', str(tmp_path/'opf')]) == 0
    assert main(['steady-study', 'export', str(tmp_path/'opf'), '--out', str(tmp_path/'study.zip')]) == 0
    text = source.read_text(); wrong = tmp_path/'wrong.m'; wrong.write_text(text.replace('mpc.baseMVA = 100', 'mpc.baseMVA = -100'))
    # The polynomial parser refuses unsupported model types instead of misreading breakpoints.
    minimal = tmp_path/'minimal.m'; minimal.write_text("mpc.version='2';"+'mpc.baseMVA=100; mpc.bus=[1 3 0 0 0 0 1 1 0 10 1 1.2 .8]; mpc.gen=[1 0 0 1 -1 1 100 1 1 0]; mpc.branch=[]; mpc.gencost=[1 0 0 1 0];')
    with pytest.raises(ValueError, match='polynomial'): load_case(minimal)


def test_opf_dispatch_replays_in_pf_and_n1_with_preserved_branch_ids(tmp_path):
    from power_grid_env.matpower_case import load_case
    from power_grid_env.balanced_ac import dispatch_case
    from power_grid_env.steady_study import derive_dispatch_study
    source = Path('cases/reference/balanced_ac_three_bus.m')
    case = load_case(source)
    result = run_steady_study(study(case, 'opf'), tmp_path/'opf')
    assert result['success']
    transferred = dispatch_case(case, result)
    pf = solve_power_flow(transferred)
    assert pf['converged'] and pf['within_limits']
    np.testing.assert_allclose(pf['vm_pu'], result['vm_pu'], atol=1e-7)
    assert transferred.branches == case.branches
    assert [g.bus for g in transferred.generators] == [g.bus for g in case.generators]
    derived = derive_dispatch_study(tmp_path/'opf', 'n1')
    assert derived['derived_from']['manifest_sha256'] == verify(tmp_path/'opf')
    n1 = run_steady_study(derived, tmp_path/'n1')
    assert n1['completed'] and n1['total_cases'] == sum(b.status for b in case.branches)
    bad = {**result, 'pg_mw': [float('nan')]*len(case.generators)}
    with pytest.raises(ValueError, match='feasibility'): dispatch_case(case, bad)


def test_n1_semantics_separate_security_from_completion_and_check_input_mutation(tmp_path):
    case, _ = exact_case()
    tight = replace(case, branches=[replace(b, rate_a=.01) for b in case.branches])
    result = run_steady_study(study(tight), tmp_path/'tight')
    assert result['completed'] and not result['secure']
    assert result['failed_or_limit_violating_cases'] == len(case.branches)
    parent = tmp_path/'parent'; run_steady_study(study(case), parent, max_cases=1)
    (parent/'cases.jsonl').write_text('')
    with pytest.raises(ValueError, match='changed'):
        run_steady_study(study(case), tmp_path/'corrupt', resume_from=parent)
    assert not (tmp_path/'corrupt').exists()


def test_resume_directory_cannot_mutate_parent_and_unused_budgets_rejected(tmp_path):
    case, _ = exact_case(); doc = study(case)
    parent = tmp_path/'parent'; run_steady_study(doc, parent, max_cases=1); before = verify(parent)
    with pytest.raises(ValueError, match='disjoint'):
        run_steady_study(doc, parent/'child', resume_from=parent)
    assert verify(parent) == before
    with pytest.raises(ValueError, match='N-1-only'):
        run_steady_study(study(case, 'power_flow'), tmp_path/'bad', time_budget_s=1.)
    assert not (tmp_path/'bad').exists()


def test_matpower_generator_voltage_is_not_silently_replaced_by_bus_initial_guess(tmp_path):
    from power_grid_env.matpower_case import load_case
    source = Path('cases/reference/balanced_ac_three_bus.m').read_text()
    changed = tmp_path/'different-vg.m'
    changed.write_text(source.replace('1000 -1000 1.04 100', '1000 -1000 1.05 100'))
    case = load_case(changed)
    assert case.buses[0].vm == 1.05


@pytest.mark.parametrize('suffix', ['mpc.dcline=[1 2 0];', 'mpc.bus(1,3)=100;', 'mpc.baseMVA=50;'])
def test_matpower_does_not_silently_drop_equipment_or_later_mutations(tmp_path, suffix):
    from power_grid_env.matpower_case import load_case
    path = tmp_path/'unsupported.m'
    path.write_text(Path('cases/reference/balanced_ac_three_bus.m').read_text()+'\n'+suffix)
    with pytest.raises(ValueError): load_case(path)


def test_matpower_does_not_guess_arithmetic_or_commented_base(tmp_path):
    from power_grid_env.matpower_case import load_case
    text = Path('cases/reference/balanced_ac_three_bus.m').read_text()
    path = tmp_path/'arithmetic.m'; path.write_text(text.replace('mpc.baseMVA = 100;', 'mpc.baseMVA = 100/2;'))
    with pytest.raises(ValueError, match='baseMVA'): load_case(path)
    path = tmp_path/'comments.m'; path.write_text('% mpc.baseMVA=999;\n'+text)
    assert load_case(path).base_mva == 100.

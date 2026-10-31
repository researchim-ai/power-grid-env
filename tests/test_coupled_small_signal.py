"""Mixed motor/generator eigenanalysis, independent coordinates and guardrails."""
from pathlib import Path
import json
import numpy as np
import pytest
from scipy.optimize import linear_sum_assignment
from scipy.linalg import expm
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.coupled_pss_reference import reference


def example():return StudySpec.model_validate_json(Path('cases/studies/coupled_modal_pss.json').read_text())


def test_modal_spectrum_matches_independent_SI_and_transfer_coordinates():
    s=example();r=run_study(s);assert r['completed'],r
    independent=np.linalg.eigvals(reference(s,linearize=True))
    actual=np.array([complex(m['real_per_s'],m['imag_rad_per_s']) for m in r['modes']])
    rows,columns=linear_sum_assignment(abs(actual[:,None]-independent[None,:]))
    assert max(abs(actual[rows]-independent[columns]))<1e-4
    assert len(r['state_names'])==20 and len(set(r['state_names']))==20
    assert r['relative_matrix_change']<1e-5 and not r['time_domain_simulated']
    for mode in r['modes']:
        if mode['participation'] is not None:assert sum(p['factor'] for p in mode['participation'])==pytest.approx(1.)


def test_linear_response_predicts_small_joint_nonlinear_perturbation():
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    from power_grid_env.coupled_linearization import state_coordinates
    s=example();r=run_study(s);model=CoupledNetwork(s.network,s.coupled);state,initial=initialize_coupled(model)
    assert state is not None,initial
    indices,names=state_coordinates(model);perturbed=state.copy();perturbed[model.gen_offset]+=1e-7
    sol=solve_ivp(model.rhs,(0.,.1),perturbed,method='Radau',rtol=1e-11,atol=1e-13)
    assert sol.success
    predicted=expm(np.asarray(r['state_matrix_per_s'])*.1)@(perturbed-state)[indices]
    assert max(abs((sol.y[:,-1]-state)[indices]-predicted))<1e-10


def test_unstable_equilibrium_is_reported_instead_of_hidden_by_startup_gate():
    s=example();s.coupled.machines[0].stabilizer.KS=3000.
    r=run_study(s);assert r['completed'],r
    assert r['linear_stability']=='unstable' and r['spectral_abscissa_per_s']>1.
    s.study='coupled_rms';r=run_study(s)
    assert not r['completed'] and 'unstable_or_near_axis_coupled_equilibrium' in r['operating_point']['rejected_candidates']


def test_modal_rejects_events_boundaries_thermal_drift_and_dense_budget(monkeypatch):
    from power_grid_env.coupled_rms import CoupledFault
    from power_grid_env.induction import MotorThermalProtection
    import power_grid_env.coupled_small_signal as modal
    s=example();s.coupled.faults=[CoupledFault(id='f',node_id='plant',apply_s=.2,clear_s=.3,r_ohm=1.,x_ohm=10.)]
    assert run_study(s)['reason']=='coupled_small_signal_equilibrium_only'
    s=example();s.coupled.operating_point=None
    assert run_study(s)['reason']=='coupled_small_signal_equilibrium_only'
    s=example();s.coupled.machines[0].stabilizer.VCU=1.
    assert run_study(s)['reason']=='controller_boundary_operating_point'
    s=example();s.coupled.motors[0].thermal=MotorThermalProtection(time_constant_s=1.,trip_level=10.,initial_level=0.)
    assert run_study(s)['reason']=='nonstationary_thermal_state'
    monkeypatch.setattr(modal,'MAX_STATES',10)
    assert run_study(example())['reason']=='coupled_small_signal_state_budget'


def test_modal_system_base_invariance_worker_cli_schema_cancel_and_timeout(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    import jsonschema
    s=example();a=run_study(s);s.network.s_base_va=3e6;b=run_study(s)
    assert a['completed'] and b['completed']
    assert max(abs(np.asarray(a['state_matrix_per_s'])-b['state_matrix_per_s']).ravel())<1e-5
    w=run_study(s,isolate=True,wall_timeout_s=30.);assert w['completed'],w
    assert w['state_matrix_per_s']==b['state_matrix_per_s']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    p=tmp_path/'case.json';p.write_text(s.model_dump_json())
    assert main(['run-study',str(p),'--out',str(tmp_path/'modal.json'),'--isolate','--wall-timeout','30'])==0
    export_schemas(tmp_path/'schemas');jsonschema.validate(s.model_dump(mode='json'),json.loads((tmp_path/'schemas/study.schema.json').read_text()))

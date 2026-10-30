"""DAE modal analysis against analytic and independent ODE linearizations."""
import json
import numpy as np
import pytest
from scipy.optimize import linear_sum_assignment
from test_canonical_rms import example
from test_genrou_adapter import spec as genrou_spec
from test_regulated_canonical_rms import controlled
from benchmarks.genrou_reference import independent_reference
from power_grid_env.small_signal import analyze_matrix
from power_grid_env.study_spec import run_study


def eigenvalues(report):return np.array([complex(m['real_per_s'],m['imag_rad_per_s']) for m in report['modes']])


def test_classical_frequency_and_damping_match_analytic_equation():
    s=example();s.study='small_signal';s.rms.events=[]
    r=run_study(s);assert r['completed'],r
    theta=np.arcsin(.5*.3/1.05);v=1.05*np.exp(1j*theta);e=v+1j*.3*(v-1)/(.3j)
    frequency=np.sqrt(abs(e)/.6*np.cos(np.angle(e))*2*np.pi*50/8)
    assert np.sort(eigenvalues(r).imag)==pytest.approx([-frequency,frequency],abs=1e-6)
    assert r['linear_stability']=='marginal_or_numerically_near_axis'
    assert r['state_names']==['g.delta','g.omega']
    for mode in r['modes']:
        assert sum(p['factor'] for p in mode['participation'])==pytest.approx(1.)
        assert all(p['factor']==pytest.approx(.5) for p in mode['participation'])
    s.rms.machines[0].damping_pu=1.
    damped=run_study(s);assert damped['completed']
    assert eigenvalues(damped).real==pytest.approx([-1/16,-1/16],abs=1e-6)
    assert damped['linear_stability']=='asymptotically_stable_linearized'


@pytest.mark.parametrize('saturated',[False,True])
def test_genrou_modes_match_jacobian_of_independent_six_state_ode(saturated):
    s=genrou_spec(saturated);s.study='small_signal';s.rms.events=[];s.rms.faults=[]
    r=run_study(s);assert r['completed'],r
    independent=independent_reference(s,[],linearize=True)
    reference=np.linalg.eigvals(independent)
    actual=eigenvalues(r);i,j=linear_sum_assignment(abs(actual[:,None]-reference))
    assert max(abs(actual[i]-reference[j]))<1e-5
    assert r['eigenpair_residual']<1e-8
    assert r['state_names']==['g.delta','g.omega','g.e1q','g.e1d','g.e2d','g.e2q']


def test_participation_normalization_coordinate_scaling_and_degenerate_modes():
    matrix=np.array([[-1.,2.],[-3.,-2.]])
    a=analyze_matrix(matrix,['one','two'])
    scale=np.diag([10.,.1])
    b=analyze_matrix(scale@matrix@np.linalg.inv(scale),['one','two'])
    assert eigenvalues(a)==pytest.approx(eigenvalues(b))
    for left,right in zip(a['modes'],b['modes']):
        assert {p['state']:p['factor'] for p in left['participation']}==pytest.approx({p['state']:p['factor'] for p in right['participation']})
    repeated=analyze_matrix(-np.eye(2),['a','b'])
    assert repeated['participation_status']=='inconclusive_repeated_modes'
    assert all(m['participation'] is None for m in repeated['modes'])
    defective=analyze_matrix([[-1.,1.],[0.,-1.]],['a','b'])
    assert defective['participation_status']=='inconclusive_ill_conditioned'
    assert analyze_matrix([[.1]],['g'])['linear_stability']=='unstable'
    with pytest.raises(ValueError):analyze_matrix([[np.nan]],['g'])
    with pytest.raises(ValueError):analyze_matrix([[1j]],['g'])


def test_isolated_modal_result_and_cli_match_direct(tmp_path):
    s=genrou_spec();s.study='small_signal';s.rms.events=[];s.rms.faults=[]
    a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert eigenvalues(a)==pytest.approx(eigenvalues(b),abs=1e-10)
    path=tmp_path/'spec.json';path.write_text(s.model_dump_json())
    from power_grid_env.cli import main
    assert main(['run-study',str(path),'--out',str(tmp_path/'modes.json')])==0
    assert json.loads((tmp_path/'modes.json').read_text())['completed']


def test_time_events_rejected_and_controller_boundary_marked_inconclusive():
    s=example();s.study='small_signal'
    assert run_study(s)['reason']=='small_signal_operating_point_only'
    s=controlled();s.study='small_signal';s.rms.events=[]
    s.rms.machines[0].governor.VMAX=.5
    r=run_study(s);assert r['completed'],r
    assert 'g.governor_valve' in r['controller_boundary_flags']
    assert r['linear_stability']=='inconclusive_at_controller_boundary'

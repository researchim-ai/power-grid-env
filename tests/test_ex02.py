"""Declared absolute OEL contract and ESST1A external PSS/modal boundaries."""
import json,os
from pathlib import Path
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.excitation_takeover import TakeoverOELPassport,quantities,rates
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.ex02_cases import example,modal,MODAL,NAMES
from benchmarks.ex02_validation import compare_modes

@pytest.mark.parametrize('bad',[{'sensor_time_s':0},{'tracking_time_s':0},{'integral_gain':0},{'proportional_gain':-1},{'current_limit_pu':float('nan')},{'minimum_field_pu':4},{'maximum_field_pu':float('inf')},{'model':'OEL2C'}])
def test_invalid_absolute_profile(bad):
    with pytest.raises(ValidationError):TakeoverOELPassport(generator_id='g',**bad)

@pytest.mark.parametrize('current',[.2,1.1,1.199])
def test_back_calculation_stationary_inactive_branch(current):
    p=TakeoverOELPassport(generator_id='g');e=p.current_limit_pu-current
    x=np.array([current,p.maximum_field_pu+(p.integral_gain*p.tracking_time_s-p.proportional_gain)*e])
    assert rates(p,x,current)==pytest.approx([0,0],abs=2e-14)
    assert quantities(p,x,current)['ceiling_pu']==p.maximum_field_pu

@pytest.mark.parametrize('integral,expected',[(100,4),(-100,0),(.5,.5)])
def test_absolute_cap_and_integrator_tracking(integral,expected):
    p=TakeoverOELPassport(generator_id='g');x=np.array([p.current_limit_pu,integral]);q=quantities(p,x,p.current_limit_pu)
    assert q['ceiling_pu']==expected
    assert rates(p,x,p.current_limit_pu)[1]==pytest.approx((expected-integral)/p.tracking_time_s)

def test_overload_is_actual_closed_feedback_not_clamped_measurement():
    s=example('overload_recovery');r=run_study(s);assert r['completed'],r
    c=r['channels'];assert max(c['g2.ESST1A.oel_active'])==1
    assert min(c['g2.TakeoverOEL.ceiling_pu'])<1
    assert max(c['g2.TakeoverOEL.field_current_pu'])>1.2
    assert np.asarray(c['g2.TakeoverOEL.field_current_pu'])==pytest.approx(c['g2.ESST1A.field_current_pu'])
    assert np.asarray(c['g2.ESST1A.effective_oel_ceiling_pu'])==pytest.approx(np.minimum(c['g2.ESST1A.oel_ceiling_pu'],c['g2.TakeoverOEL.ceiling_pu']))

@pytest.mark.parametrize('name',MODAL)
def test_modal_supported_interior_profiles(name):
    r=run_study(modal(name));assert r['completed'],r
    assert r['branch_preserving_steps'] and r['relative_matrix_change']<1e-5
    assert len(r['state_names'])==len(r['modes'])
    assert sum('.TakeoverOEL.' in n for n in r['state_names'])==(2 if 'oel' in name else 0)

def test_uel_hybrid_not_misrepresented_as_ordinary_modal_jacobian():
    s=example('hold');s.study='coupled_small_signal';r=run_study(s)
    assert not r['completed'] and r['reason']=='underexcitation_limiter_modal_analysis_unsupported'

@pytest.mark.parametrize('change',['missing_avr','implicit_pss','duplicate_oel','wrong_target','hot_initial_limit'])
def test_connections_and_initial_boundaries_fail_closed(change):
    s=example('hold')
    if change=='missing_avr':s.coupled.esst1a_exciters=[]
    if change=='implicit_pss':s.coupled.machines[1].stabilizer_connection='SEXS'
    if change=='duplicate_oel':s.coupled.takeover_oels*=2
    if change=='wrong_target':s.coupled.takeover_oels[0].generator_id='g'
    if change=='hot_initial_limit':s.coupled.takeover_oels[0].current_limit_pu=.1
    try:r=run_study(StudySpec.model_validate(s.model_dump(mode='json')))
    except (ValidationError,ValueError):return
    assert not r['completed'],r

@pytest.mark.parametrize('bad',[float('nan'),float('inf')])
def test_modal_comparator_rejects_nonfinite(bad):
    with pytest.raises(ValueError):compare_modes({'completed':True,'modes':[{'real_per_s':bad,'imag_rad_per_s':0}]},{'completed':True,'eigenvalues_per_s':[[0,0]],'state_count':1})

def test_modal_comparator_cannot_hide_missing_modes():
    with pytest.raises(ValueError):compare_modes({'completed':True,'modes':[]},{'completed':True,'eigenvalues_per_s':[],'state_count':0})

@pytest.mark.parametrize('name',NAMES)
def test_registered_case_roundtrip(name):
    s=example(name);assert StudySpec.model_validate_json(s.model_dump_json())==s

@pytest.mark.parametrize('name',MODAL)
def test_native_modal_independent_coordinates(name,tmp_path,monkeypatch):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned native reference')
    from benchmarks.esst1a_network import run
    s=modal(name);a=run_study(s);s.study='coupled_rms'
    def forbidden(*args,**kwargs):raise AssertionError('production dynamics used by native modal oracle')
    monkeypatch.setattr('power_grid_env.coupled_rms.CoupledNetwork.rhs',forbidden)
    monkeypatch.setattr('power_grid_env.exciter_esst1a.rates',forbidden)
    monkeypatch.setattr('power_grid_env.excitation_takeover.rates',forbidden)
    monkeypatch.setattr('power_grid_env.coupled_pss.CoupledPSS.evaluate',forbidden)
    b=run(s,tmp_path/'native',Path(root),1e-11,linearize=True)
    assert compare_modes(a,b)['passed']
    assert not b['production_stamps_used'] and not b['primary_state_vector_imported']

@pytest.mark.parametrize('frequency',[.01,.1,1.,10.,100.])
def test_pss_cascade_matches_independent_rational_frequency_response(frequency):
    from power_grid_env.coupled_pss import CoupledPSS
    s=example('hold');m=next(m for m in s.coupled.machines if m.generator_id=='g2');p=m.stabilizer
    bank=CoupledPSS([m],{0:slice(0,7)})
    def evaluate(x,u):
        d,_,q=bank.evaluate(x,np.array([1.+u]),np.array([1.+0j]));return d[0],q['g2.IEEEST.raw_pu']
    zero=np.zeros(7);a=np.column_stack([evaluate(np.eye(7)[i],0)[0] for i in range(7)])
    b=evaluate(zero,.5)[0]/.5;c=np.array([evaluate(np.eye(7)[i],0)[1] for i in range(7)]);d=evaluate(zero,.5)[1]/.5
    z=1j*frequency
    expected=(p.A6*z*z+p.A5*z+1)/((p.A2*z*z+p.A1*z+1)*(p.A4*z*z+p.A3*z+1))*(p.T1*z+1)/(p.T2*z+1)*(p.T3*z+1)/(p.T4*z+1)*p.KS*p.T5*z/(p.T6*z+1)
    assert c@np.linalg.solve(z*np.eye(7)-a,b)+d==pytest.approx(expected,rel=1e-11,abs=1e-12)

@pytest.mark.parametrize('name',['avr','avr_pss_oel'])
def test_modal_matrix_predicts_small_nonlinear_perturbation(name):
    from scipy.integrate import solve_ivp
    from scipy.linalg import expm
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    from power_grid_env.coupled_linearization import state_coordinates
    s=modal(name);r=run_study(s);m=CoupledNetwork(s.network,s.coupled);x,a=initialize_coupled(m);assert x is not None,a
    indices,_=state_coordinates(m);y=x.copy();y[m.gen_offset]+=1e-7
    sol=solve_ivp(m.rhs,(0.,.05),y,method='Radau',rtol=1e-11,atol=1e-13);assert sol.success
    predicted=expm(np.asarray(r['state_matrix_per_s'])*.05)@(y-x)[indices]
    assert max(abs((sol.y[:,-1]-x)[indices]-predicted))<1e-10


@pytest.mark.parametrize('bad',[(float('nan'),1.),(1.,0.),(1.,float('inf'))])
def test_native_auxiliary_rejects_invalid_experiment_before_artifacts(bad,tmp_path):
    from benchmarks.ex02_native_blocks import NativeAux
    out=tmp_path/'must-not-exist'
    with pytest.raises(ValueError):NativeAux(TakeoverOELPassport(generator_id='g'),'oel',bad[0],1.,out,tmp_path/'missing-runtime',bad[1],1e-11)
    assert not out.exists()

@pytest.mark.parametrize('name',NAMES)
def test_checked_in_cases_match_registered_protocol(name):
    p=Path('cases/studies')/('ex02_'+name+'.json')
    assert StudySpec.model_validate_json(p.read_text())==example(name)

@pytest.mark.parametrize('name',MODAL)
def test_checked_in_modal_cases_match_registered_protocol(name):
    p=Path('cases/studies')/('ex02_modal_'+name+'.json')
    assert StudySpec.model_validate_json(p.read_text())==modal(name)


def test_missing_optional_andes_does_not_inject_local_overlay(monkeypatch):
    import sys
    from power_grid_env import andes_rms
    before=list(sys.path)
    monkeypatch.setattr(andes_rms.importlib.util,'find_spec',lambda name:None)
    assert andes_rms.ensure_andes_importable() is False
    assert sys.path==before


def test_modal_reports_unstable_external_esst1a_pss_equilibrium():
    s=modal('avr_pss_oel');s.coupled.esst1a_exciters[0].K_A=20.
    next(m for m in s.coupled.machines if m.stabilizer).stabilizer.KS=3000.
    r=run_study(s);assert r['completed'] and r['linear_stability']=='unstable'
    assert r['spectral_abscissa_per_s']>0
    s.study='coupled_rms';r=run_study(s);assert not r['completed']


def test_external_profile_exported_schema_and_isolated_worker(tmp_path):
    import jsonschema
    from power_grid_env.schema_export import export_schemas
    export_schemas(tmp_path)
    s=modal('avr_pss_oel');jsonschema.validate(s.model_dump(mode='json'),json.loads((tmp_path/'study.schema.json').read_text()))
    r=run_study(s,isolate=True,wall_timeout_s=30.)
    assert r['completed'] and r['branch_preserving_steps']


def test_pss_limit_scenario_evidence_is_strict_json():
    from benchmarks.ex02_validation import scenario_checks
    r=scenario_checks('pss_limited',{'channels':{'g2.IEEEST.limited_pu':[0.,.0002]}},{})
    assert r['passed'] and json.loads(json.dumps(r,allow_nan=False))==r

@pytest.mark.parametrize('kind',['oel_current','oel_tracking_margin','pss_voltage','pss_output'])
def test_native_auxiliary_rejects_nondifferentiable_initial_branch_before_compilation(kind,tmp_path):
    from benchmarks.ex02_native_blocks import NativeAux
    m=next(m for m in example('hold').coupled.machines if m.stabilizer)
    p=m.stabilizer if kind.startswith('pss') else TakeoverOELPassport(generator_id='g')
    current=0.;voltage=1.
    if kind=='oel_current':current=p.current_limit_pu
    if kind=='oel_tracking_margin':p.integral_gain=1e-8;current=1.1
    if kind=='pss_voltage':voltage=p.VCL
    if kind=='pss_output':p.LSMIN=0.
    out=tmp_path/'not-created'
    with pytest.raises(ValueError,match='native initial (PSS|takeover OEL).*boundary'):
        NativeAux(p,'pss' if kind.startswith('pss') else 'oel',current,voltage,out,tmp_path/'missing-runtime',1.,1e-11)
    assert not out.exists()


def test_event_policy_changes_guards_without_changing_continuous_ode():
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter,mnlex2_rates,MinimumExcitationBank
    a=UnderexcitationLimiter(generator_id='g');b=a.model_copy(update={'event_detection':'conditioned_1e-8'})
    for state in ([-.03,0.],[.2,.1],[.01,.02]):
        assert np.array_equal(mnlex2_rates(a,state,.02),mnlex2_rates(b,state,.02))
    bank=MinimumExcitationBank.__new__(MinimumExcitationBank);bank.entries={0:(0,b)};bank.regions={0:-1};bank.free_rate=lambda x,i:x[0]
    ids,guards=bank.guards(np.array([0.]))
    assert guards[0](0.,np.array([.5e-8]))<0
    assert guards[0](0.,np.array([1e-8]))==0
    assert guards[0](0.,np.array([2e-8]))>0


def test_frozen_weak_clock_is_rejected_and_never_qualifies_current_code(tmp_path):
    from benchmarks.ex02_conditioning import frozen_rejection
    r=frozen_rejection(tmp_path)
    assert r['negative_regression_passed'] and not r['trajectory_qualified'] and not r['qualifies_current_producer']
    assert all(row['maximum_reset_time_error_s']>1e-6 for row in r['runs'])


def test_changed_frozen_witness_fails_before_writing_evidence(tmp_path,monkeypatch):
    import benchmarks.ex02_conditioning as module
    lock=json.loads(module.LOCK.read_text());lock['files'][next(iter(lock['files']))]='0'*64
    p=tmp_path/'lock.json';p.write_text(json.dumps(lock));monkeypatch.setattr(module,'LOCK',p)
    with pytest.raises(ValueError,match='changed or missing'):module.frozen_rejection(tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_conditioned_policy_requires_matching_native_evidence():
    from benchmarks.mnlex2_guard_audit import audit
    s=example('pss_limited')
    r=dict(completed=True,native_UEL_event_policy='causal',native_MNLEX2_in_feedback=True,event_log=[],native_UEL_root_policy='continuous_atoms')
    with pytest.raises(ValueError,match='conditioned UEL event policy evidence'):audit(s,r)

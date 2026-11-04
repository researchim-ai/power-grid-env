"""Active OEL2C authority, explicit AVR references and independent native data."""
from functools import lru_cache
from pathlib import Path
import copy,os
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics,OEL2CBank
from power_grid_env.study_spec import run_study,StudySpec
from benchmarks.generator_oel2c_active_cases import example,NAMES
from benchmarks.generator_oel2c_active_validation import negative,NEGATIVE
from benchmarks.generator_oel2c_validation import compare
from benchmarks.generator_oel2c_loaded_validation import compare_initial_references


def passport(**changes):
    return OEL2CPassport(**(dict(generator_id='g',rated_field_reaction_pu=1.,initialization='active_limited_equilibrium',avr_reference_initialization='derive_from_operating_point_with_OEL')|changes))


@pytest.mark.parametrize('current',[1.2,1.4,2.,4.])
@pytest.mark.parametrize('changes',[{},dict(Sw1=True),dict(VOel3MinPu=-.05),dict(VOel2MinPu=-.03),dict(tC1Oel=.08,tC2Oel=.09),dict(KpOel=2.,KdOel=.04,KiOel=8.),dict(tEn=0.)])
def test_all_active_rates_solve_the_projected_equations(current,changes):
    p=passport(**changes);k=OEL2CDynamics(p);x=k.initial(current);q=k.quantities(x)
    assert max(abs(k.rhs(x,current)))<1e-10
    assert x[2]==p.ILimPu and q['pid']==p.VOel3MinPu and q['raw']<p.VOel3MinPu
    expected=max(max(p.VOel3MinPu,p.VOel2MinPu),p.VOel1MinPu)
    assert q['output']==pytest.approx(expected,abs=1e-14) and q['error']<0
    assert k.context['reference_bound']==-1 and k.context['enable_expired']
    assert k.context['enable_start']==-(p.tEn+1.) and k.context['reset_start'] is None
    saved=x.copy();context=copy.deepcopy(k.context);k.rhs(x,current+.01)
    assert np.array_equal(saved,x) and k.context==context
    log=[];k.apply(set(),0.,x,log);assert not log


@pytest.mark.parametrize('current,changes',[(.8,{}),(1.,{}),(1.01,{}),(1.4,dict(KAct=.1)),(1.4,dict(VInvMaxPu=-1.)),(1.4,dict(Krd=0.)),(1.4,dict(Sw1=True,K1=0.)),(1.4,dict(tFcl=1.06)),(float('nan'),{})])
def test_invalid_active_branch_is_not_relabelled_stationary(current,changes):
    with pytest.raises(ValueError):OEL2CDynamics(passport(**changes)).initial(current)


@pytest.mark.parametrize('changes',[dict(initialization='active_limited_equilibrium',avr_reference_initialization='unmodified'),dict(initialization='inactive_equilibrium'),dict(initialization='dynawo_startup')])
def test_reference_changes_require_an_explicit_compatible_policy(changes):
    with pytest.raises(ValidationError):passport(**changes)


@pytest.fixture(scope='module')
def calculated():
    @lru_cache(None)
    def evaluate(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=120.);assert r['completed'],r
        return s,r
    return evaluate


@pytest.fixture(scope='module')
def native_calculated(tmp_path_factory):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference not configured')
    from benchmarks.oel2c_model_exchange import run
    folder=tmp_path_factory.mktemp('oel2c-active')
    @lru_cache(None)
    def evaluate(name):return run(example(name),folder/name,Path(root),1e-10)
    return evaluate


@pytest.mark.parametrize('name',NAMES)
def test_operating_point_records_actual_reference_and_exhausted_authority(name,calculated):
    s,r=calculated(name);op=r['operating_point'];audit=op['oel2c_AVR_reference_initialization']['g2']
    assert op['checked_dynamic_residual_per_s']<1e-8
    assert max(op['oel2c_stationary_residual_per_s'].values())<1e-8
    assert audit['derived_reference_pu']==pytest.approx(audit['baseline_reference_pu']-audit['limiter_output_pu'])
    assert audit['current_excess_pu']>0 and audit['PID_authority_exhausted']
    assert r['channels']['g2.SEXS.reference_pu'][0]==audit['derived_reference_pu']


@pytest.mark.parametrize('name',NAMES)
def test_native_authority_and_reference_are_independently_initialized(name,calculated,native_calculated):
    s,r=calculated(name);native=native_calculated(name)
    assert compare(s,r,native)['passed']
    assert compare_initial_references(s,r,native)['passed']


@pytest.mark.parametrize('name',NEGATIVE)
def test_bad_reference_or_equilibrium_never_returns_partial_success(name):
    data=negative(name)
    try:s=StudySpec.model_validate(data)
    except ValidationError:return
    r=run_study(s,isolate=True,wall_timeout_s=120.)
    assert not r['completed'] and 'channels' not in r


def test_hold_is_stationary_but_does_not_claim_current_limit_achievement(calculated):
    s,r=calculated('hold');assert not r['event_log']
    for key in ['g2.OEL2C.output_pu','g2.OEL2C.timer_s','g2.OEL2C.pid_integral_pu','g2.SEXS.field_pu','g2.SEXS.reference_pu']:
        assert np.ptp(r['channels'][key])<1e-9
    assert min(r['channels']['g2.OEL2C.actual_current_pu'])>s.coupled.oel2c_limiters[0].ILimPu


def test_voltage_command_releases_the_limiter_without_reinitializing_reference(calculated):
    s,r=calculated('voltage_down');ch=r['channels'];audit=r['operating_point']['oel2c_AVR_reference_initialization']['g2']
    assert ch['g2.OEL2C.output_pu'][0]<-.1 and abs(ch['g2.OEL2C.output_pu'][-1])<1e-8
    assert ch['g2.SEXS.reference_pu'][-1]==pytest.approx(audit['derived_reference_pu']-.25)


def test_mixed_modes_only_shift_the_requested_machine(calculated):
    s,r=calculated('mixed_modes');assert set(r['operating_point']['oel2c_AVR_reference_initialization'])=={'g2'}
    assert max(abs(np.asarray(r['channels']['g.OEL2C.output_pu'])))<1e-12


def test_initial_field_thermal_memory_is_separate_from_electromechanical_equilibrium(calculated):
    _,trip=calculated('field_trip');_,warm=calculated('field_equilibrium')
    assert trip['operating_point']['generator_field_thermal_residual_per_s']['g2']>1
    assert abs(warm['operating_point']['generator_field_thermal_residual_per_s']['g2'])<1e-10
    assert any(e['kind']=='generator_field_thermal_trip' for e in trip['event_log'])
    from benchmarks.generator_field_validation import analytic_quench
    assert analytic_quench(example('field_trip'),trip)['passed']


@pytest.mark.parametrize('factor',[.1,7.])
def test_active_reference_and_field_base_survive_network_VA_scaling(factor,calculated):
    s,r=calculated('hold');s=s.model_copy(deep=True);s.network.s_base_va*=factor;b=run_study(s);assert b['completed']
    for key in ['g2.SEXS.reference_pu','g2.OEL2C.actual_current_pu','g2.OEL2C.output_pu']:
        assert b['channels'][key]==pytest.approx(r['channels'][key],abs=1e-8)


def test_missing_native_AVR_reference_cannot_hide_behind_matching_field(calculated,native_calculated):
    s,r=calculated('hold');ref=copy.deepcopy(native_calculated('hold'));del ref['channels']['g2.SEXS.reference_pu']
    with pytest.raises(ValueError,match='missing native'):compare(s,r,ref)


def test_native_active_initialization_does_not_use_production_AVR_or_OEL_initialization(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference not configured')
    from benchmarks.oel2c_model_exchange import run
    from power_grid_env.coupled_controls import CoupledControllerBank
    def forbidden(*args,**kwargs):raise AssertionError('production equilibrium or AVR initialization used')
    for name in ['initial','active_limited_equilibrium','rhs','quantities','apply','signals','guards']:monkeypatch.setattr(OEL2CDynamics,name,forbidden)
    monkeypatch.setattr(OEL2CBank,'coordinate_initial_avr_reference',forbidden);monkeypatch.setattr(CoupledControllerBank,'initialize',forbidden)
    s=example('hold');s.coupled.duration_s=.05;r=run(s,tmp_path/'independent',Path(root),1e-10)
    assert r['completed'] and r['AVR_reference_initialization']['g2']['limiter_output_pu']<0


def test_independent_horizon_extension_is_explicit_and_does_not_truncate():
    from benchmarks.oel2c_model_exchange import scope
    from benchmarks.generator_excitation_si import ExcitationCircuit
    baseline=scope(example('voltage_down'))
    with pytest.raises(ValueError,match='horizon'):ExcitationCircuit(baseline)
    plant=ExcitationCircuit(baseline,maximum_duration_s=5.)
    assert plant.s.duration_s==5.


def test_active_policy_preserves_physical_initial_point_and_exposes_reference_shift(calculated):
    s,r=calculated('hold');baseline=s.model_copy(deep=True);baseline.coupled.oel2c_limiters=[]
    b=run_study(baseline);assert b['completed']
    for gid in ['g','g2']:assert r['operating_point']['generator_initial'][gid]==pytest.approx(b['operating_point']['generator_initial'][gid])
    shift=r['channels']['g2.SEXS.reference_pu'][0]-b['channels']['g2.SEXS.reference_pu'][0]
    assert shift==pytest.approx(-r['channels']['g2.OEL2C.output_pu'][0])


def test_reference_audit_corruption_is_detected(calculated,native_calculated):
    s,r=calculated('hold');b=copy.deepcopy(native_calculated('hold'))
    b['AVR_reference_initialization']['g2']['derived_reference_pu']+=.01
    with pytest.raises(ValueError,match='references differ'):compare_initial_references(s,r,b)


def test_initialization_policy_is_not_emitted_as_a_Modelica_parameter():
    params=passport().native_parameters()
    assert 'avr_reference_initialization' not in params and 'initialization' not in params
    assert all(isinstance(v,(float,bool)) for v in params.values())


def test_numerical_bounds_are_not_reported_as_current_safety_acceptance(calculated):
    _,r=calculated('hold');d=r['additional_validation']['oel2c_limits']['g2']
    assert d['within_limits'] and not d['current_acceptance_evaluated']
    assert d['maximum_thermal_reference_excess_pu']>.5
    assert d['PID_lower_bound_with_negative_error_samples']==len(r['time_s'])
    assert 'not field-current acceptance' in d['limits_scope']


def test_native_scope_rejects_inconsistent_field_bases_before_compilation():
    from benchmarks.oel2c_model_exchange import scope
    s=StudySpec.model_validate(negative('field_base'))
    with pytest.raises(ValueError,match='current bases must agree'):scope(s)

"""Complementarity, explicit memory, network feedback and independent native starts."""
import copy,json,os
from pathlib import Path
from functools import lru_cache
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.generator_oel2c_selected_cases import NAMES,example
from benchmarks.generator_oel2c_selected_validation import NEGATIVE,negative,initial_audit
from benchmarks.generator_oel2c_validation import compare


def passport(**changes):
    return OEL2CPassport(**(dict(generator_id='g',rated_field_reaction_pu=1.,model='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation',initialization='selected_equilibrium',avr_reference_initialization='derive_from_operating_point_with_OEL',equilibrium_stability_policy='stationarity_only')|changes))

@pytest.fixture(scope='module')
def primary():
    @lru_cache(None)
    def result(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=120.);assert r['completed'],r
        return s,r
    return result

@pytest.mark.parametrize('name',NAMES)
def test_selected_network_has_original_zero_derivatives_and_truthful_scope(name,primary):
    s,r=primary(name);op=r['operating_point'];assert op['checked_dynamic_residual_per_s']<1e-8
    assert op['full_OEL_stability_qualified'] is False
    assert op['stationary_stability_scope']=='machine_controller_subset_only_selected_OEL_stability_not_qualified'
    assert set(op['oel2c_selected_equilibrium'])=={p.generator_id for p in s.coupled.oel2c_limiters}
    for gid,audit in op['oel2c_selected_equilibrium'].items():
        assert audit['maximum_derivative_per_s']<1e-8
        a=op['oel2c_AVR_reference_initialization'][gid]
        assert a['derived_reference_pu']==pytest.approx(a['baseline_reference_pu']-audit['output_pu'])
    json.dumps(op,allow_nan=False)

@pytest.mark.parametrize('name',NAMES)
def test_independent_native_all_states_and_network_channels(name,primary,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native OEL reference')
    from benchmarks.oel2c_model_exchange import run
    s,r=primary(name);native=run(s,tmp_path/'native',Path(root),1e-12)
    c=compare(s,r,native);assert c['passed'],{k:e for k,e in c['errors'].items() if e>c['limits'][k]}
    assert initial_audit(s,r,native)['passed']
    assert native['conditional_regulating_spectrum'] is None

@pytest.mark.parametrize('name',NEGATIVE)
def test_missing_or_contradictory_memory_is_rejected_without_trace(name):
    try:s=StudySpec.model_validate(negative(name))
    except ValidationError:return
    r=run_study(s,isolate=True,wall_timeout_s=120.);assert not r['completed'] and 'channels' not in r

@pytest.mark.parametrize('current',[0.,.5,.8,1.,1.3,1.6,2.])
@pytest.mark.parametrize('inverse',[False,True])
def test_active_set_projected_equations_and_pure_RHS(current,inverse):
    p=passport(Sw1=inverse)
    if current==1. and inverse:p.equilibrium_reference_current_pu=1.4
    k=OEL2CDynamics(p);x=k.initial(current)
    assert max(abs(k.rhs(x,current)))<1e-8
    assert p.ILimPu<=x[2]<=p.IInstPu
    q=k.quantities(x);assert p.VOel1MinPu<=q['output']<=0
    for label,a,lo,hi in [('timer',1,p.tMin,p.tMax),('reference',2,p.ILimPu,p.IInstPu)]:
        rate=k.free_rates(x)[label];mode=k.context[label+'_bound']
        assert (rate<=0 and x[a]==lo) if mode==-1 else (rate>=0 and x[a]==hi) if mode==1 else abs(rate)<1e-12
    y=x.copy();context=copy.deepcopy(k.context);k.rhs(x,current+.01)
    assert np.array_equal(x,y) and context==k.context
    logs=[];k.apply(set(),0.,x,logs);assert not logs

@pytest.mark.parametrize('memory',[0.,-.08,-.4])
def test_neutral_PID_memory_is_preserved_and_never_silently_selected(memory):
    p=passport(Kru=0.,Krd=0.,IThOffPu=2.,equilibrium_reference_current_pu=.8,equilibrium_pid_output_pu=memory)
    k=OEL2CDynamics(p);x=k.initial(.8);assert x[4]==memory
    assert k.initialization_audit['neutral_PID_memory'] and k.initialization_audit['neutral_reference_memory']
    p.equilibrium_pid_output_pu=None
    with pytest.raises(ValueError,match='ambiguous zero-error PID memory'):OEL2CDynamics(p).initial(.8)

@pytest.mark.parametrize('name', ['timer_lower_contact','timer_upper_contact'])
def test_zero_free_timer_contact_has_no_artificial_transition(name,primary):
    _,r=primary(name);a=r['operating_point']['oel2c_selected_equilibrium']['g2']
    assert a['timer_active_set']!=0 and a['timer_free_rate']==0.
    assert not r['event_log']
    assert np.ptp(r['channels']['g2.OEL2C.timer_s'])<1e-10

def test_neutral_memory_can_select_distinct_stationary_points(primary):
    _,a=primary('zero_error_zero_output');_,b=primary('zero_error_interior')
    assert a['channels']['g2.terminal_voltage_pu'][0]==pytest.approx(b['channels']['g2.terminal_voltage_pu'][0],abs=1e-12)
    assert a['channels']['g2.OEL2C.pid_integral_pu'][0]!=b['channels']['g2.OEL2C.pid_integral_pu'][0]
    assert a['channels']['g2.SEXS.reference_pu'][0]!=b['channels']['g2.SEXS.reference_pu'][0]

@pytest.mark.parametrize('kwargs',[dict(equilibrium_stability_policy='default_checks'),dict(model='OEL2C_Dynawo_cap_guard_v1_SEXS_summation'),dict(avr_reference_initialization='unmodified'),dict(equilibrium_pid_output_pu=float('nan'))])
def test_selected_policy_and_finite_inputs_are_explicit(kwargs):
    with pytest.raises(ValidationError):passport(**kwargs)


def test_native_selection_uses_no_production_initialization(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native OEL reference')
    import power_grid_env.oel2c_stationary as selected
    from power_grid_env.coupled_generators import RoundRotorBank
    from benchmarks.oel2c_model_exchange import run
    def forbidden(*a,**kw):raise AssertionError('production physics used by reference')
    monkeypatch.setattr(selected,'select_stationary',forbidden);monkeypatch.setattr(OEL2CDynamics,'initial',forbidden);monkeypatch.setattr(OEL2CDynamics,'rhs',forbidden)
    monkeypatch.setattr(RoundRotorBank,'initialize',forbidden);monkeypatch.setattr(RoundRotorBank,'field_reaction',forbidden)
    s=example('zero_error_interior');s.coupled.duration_s=.02
    assert run(s,tmp_path/'native',Path(root),1e-12)['completed']

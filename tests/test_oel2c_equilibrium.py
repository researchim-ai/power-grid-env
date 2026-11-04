"""Stationary field limiting: algebraic residuals, native feedback and scope."""
from functools import lru_cache
from pathlib import Path
import copy,os
import numpy as np
import pytest
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.model import canonical_hash
from benchmarks.generator_oel2c_loaded_cases import example,NAMES
from benchmarks.generator_oel2c_loaded_validation import negative,NEGATIVE
from benchmarks.generator_oel2c_validation import compare


@pytest.fixture(scope='module')
def calculated():
    @lru_cache(None)
    def evaluate(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=120.);assert r['completed'],r
        return s,r
    return evaluate


@pytest.mark.parametrize('current',[0.,.2,.8,.99])
@pytest.mark.parametrize('changes',[{},dict(Sw1=True),dict(tMin=-.1),dict(tEn=0.),dict(IThOffPu=2.),dict(KpOel=2.,KdOel=.04,KiOel=8.)])
def test_all_eight_projected_rates_are_stationary(current,changes):
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,initialization='inactive_equilibrium',**changes)
    k=OEL2CDynamics(p);x=k.initial(current)
    assert max(abs(k.rhs(x,current)))<1e-11
    assert k.quantities(x)['output']==0 and x[2]==p.IInstPu and x[3]==p.IInstPu
    assert x[4]==pytest.approx((.9-p.KpOel)*k.quantities(x)['error'])
    assert k.context['reference_bound']==1
    if k.context['below']:
        assert k.context['reset_start']==-(p.tOff+1.) and k.context['reset_expired']
    else:assert k.context['reset_start'] is None and not k.context['reset_expired']
    before=x.copy();context=copy.deepcopy(k.context)
    k.rhs(x,current+.01)
    assert np.array_equal(x,before) and k.context==context


@pytest.mark.parametrize('current,changes',[(1.,{}),(1.1,{}),(.8,dict(IInstPu=.7)),(.8,dict(IThOffPu=.8)),(.8,dict(Kru=0.)),(.8,dict(Sw1=True,K1=0.)),(.8,dict(VInvMinPu=1.)),(.8,dict(tMin=.1)),(-.1,{})])
def test_boundary_active_or_nonunique_equilibrium_is_not_silently_projected(current,changes):
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,initialization='inactive_equilibrium',**changes)
    with pytest.raises(ValueError):OEL2CDynamics(p).initial(current)


@pytest.mark.parametrize('name',NAMES)
def test_loaded_network_checks_all_stationary_limiter_states(name,calculated):
    s,r=calculated(name);op=r['operating_point']
    assert op['mode']=='coupled_loaded_equilibrium' and op['checked_dynamic_residual_per_s']<1e-8
    assert set(op['oel2c_stationary_residual_per_s'])=={p.generator_id for p in s.coupled.oel2c_limiters}
    assert all(v<1e-8 for v in op['oel2c_stationary_residual_per_s'].values())
    assert r['channels']['g2.OEL2C.output_pu'][0]==0


@pytest.mark.parametrize('name',NAMES)
def test_loaded_network_matches_native_limiter_with_independent_initialization(name,calculated,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned Dynawo/Modelica reference not configured')
    from benchmarks.oel2c_model_exchange import run
    s,r=calculated(name);ref=run(s,tmp_path/'native',Path(root),1e-10)
    assert compare(s,r,ref)['passed']
    assert all(v['native_time_offset_s']>0 for v in ref['limiter_initialization'].values())


@pytest.mark.parametrize('name',NEGATIVE)
def test_infeasible_network_initialization_never_returns_success_channels(name):
    s=StudySpec.model_validate(negative(name));r=run_study(s,isolate=True,wall_timeout_s=120.)
    assert not r['completed'] and 'channels' not in r


def test_hold_has_no_artificial_startup_or_timer_events(calculated):
    s,r=calculated('hold');assert not r['event_log']
    for key in ['g2.OEL2C.output_pu','g2.OEL2C.timer_s','g2.OEL2C.reference_current_pu','g2.OEL2C.pid_integral_pu','g2.SEXS.field_pu']:
        assert np.ptp(r['channels'][key])<1e-9


def test_equilibrium_does_not_retune_network_passports_or_AVR_reference(calculated):
    s,r=calculated('hold');before=canonical_hash(s);baseline=s.model_copy(deep=True);baseline.coupled.oel2c_limiters=[]
    other=run_study(baseline);assert other['completed'] and canonical_hash(s)==before
    for gid in ['g','g2']:
        assert r['operating_point']['generator_initial'][gid]==pytest.approx(other['operating_point']['generator_initial'][gid])
        assert r['channels'][gid+'.SEXS.reference_pu']==pytest.approx(other['channels'][gid+'.SEXS.reference_pu'],abs=1e-12)


@pytest.mark.parametrize('factor',[.1,7.])
def test_stationary_initialization_does_not_depend_on_network_VA_base(factor,calculated):
    s,r=calculated('hold');s=s.model_copy(deep=True);s.network.s_base_va*=factor;changed=run_study(s)
    assert changed['completed']
    for key in ['g2.OEL2C.measured_current_pu','g2.OEL2C.pid_integral_pu','g2.OEL2C.output_pu']:
        assert changed['channels'][key]==pytest.approx(r['channels'][key],abs=1e-8)


def test_native_stationary_initialization_never_calls_production_equilibrium(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned Dynawo/Modelica reference not configured')
    from benchmarks.oel2c_model_exchange import run
    def forbidden(*args,**kwargs):raise AssertionError('production initialization or RHS used by native reference')
    for name in ['initial','inactive_equilibrium','rhs','quantities','apply','signals','guards']:monkeypatch.setattr(OEL2CDynamics,name,forbidden)
    s=example('hold');s.coupled.duration_s=.05;r=run(s,tmp_path/'independent',Path(root),1e-10)
    assert r['completed'] and not r['production_limiter_RHS_used']

"""Current-constrained starts, specified AVR input, v2 integrity and native poles."""
from functools import lru_cache
from pathlib import Path
import copy,os
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.generator_oel2c import OEL2CDynamics
from benchmarks.generator_oel2c_regulating_cases import example,passport,NAMES
from benchmarks.generator_oel2c_regulating_validation import negative,NEGATIVE
from benchmarks.generator_oel2c_validation import compare
from benchmarks.generator_oel2c_loaded_validation import compare_initial_references,compare_conditional_spectra


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
    out=tmp_path_factory.mktemp('oel2c-regulating')
    @lru_cache(None)
    def evaluate(name):return run(example(name),out/name,Path(root),1e-12)
    return evaluate


@pytest.mark.parametrize('name',NAMES)
def test_field_target_replaces_only_requested_PV_constraint(name,calculated):
    s,r=calculated(name);op=r['operating_point']
    assert op['mode']=='coupled_loaded_current_regulating_equilibrium'
    assert op['checked_dynamic_residual_per_s']<1e-8
    for p in s.coupled.oel2c_limiters:
        if p.initialization!='current_regulating_equilibrium':continue
        gid=p.generator_id;ch=r['channels'];a=op['oel2c_AVR_reference_initialization'][gid]
        assert abs(ch[gid+'.OEL2C.actual_current_pu'][0]-p.ILimPu)<1e-9
        assert max(p.VOel1MinPu,p.VOel2MinPu,p.VOel3MinPu)<ch[gid+'.OEL2C.output_pu'][0]<0
        assert not a['PID_authority_exhausted'] and a['derived_reference_pu']==p.specified_avr_reference_pu
        assert op['initial_voltage_constraints'][gid]['kind']=='field_current_target_replaces_PV_voltage_target'
        assert p.regulating_voltage_bounds_pu[0]<ch[gid+'.terminal_voltage_pu'][0]<p.regulating_voltage_bounds_pu[1]
    assert any('.OEL2C.pid_integral_pu' in n for n in op['conditional_regulating_state_names'])
    assert not any('.OEL2C.reference_current_pu' in n for n in op['conditional_regulating_state_names'])


@pytest.mark.parametrize('name',NAMES)
def test_native_joint_root_and_all_conditional_poles_match(name,calculated,native_calculated):
    s,r=calculated(name);ref=native_calculated(name)
    assert compare(s,r,ref)['passed']
    assert compare_initial_references(s,r,ref)['passed']
    spectrum=compare_conditional_spectra(r,ref);assert spectrum['passed'],spectrum
    assert 'equilibrium_guard_v2_sha256' in ref['reference']


@pytest.mark.parametrize('name',NEGATIVE)
def test_infeasible_regulation_does_not_change_problem_or_return_success(name):
    data=negative(name)
    try:s=StudySpec.model_validate(data)
    except ValidationError:return
    r=run_study(s,isolate=True,wall_timeout_s=120.)
    assert not r['completed'] and 'channels' not in r
    if name=='unstable_controller':assert 'unstable_or_near_axis_coupled_equilibrium' in r['operating_point']['rejected_candidates']


@pytest.mark.parametrize('output',[-.01,-.08,-.15])
@pytest.mark.parametrize('changes',[{},dict(KpOel=.2),dict(tC1Oel=.08,tC2Oel=.09),dict(tMax=.5),dict(KdOel=0.)])
def test_interior_PID_fixed_point_and_clock_band_are_stationary(output,changes):
    p=passport(**changes);k=OEL2CDynamics(p);x=k.initial(p.ILimPu/(p.KAct*p.KScale),regulating_output_pu=output)
    assert max(abs(k.rhs(x,p.ILimPu/(p.KAct*p.KScale))))<1e-10
    assert k.quantities(x)['output']==pytest.approx(output,abs=1e-14)
    assert k.context['enable_start'] is None and k.context['reset_start'] is None
    logs=[];k.apply(set(),0.,x,logs);assert not logs
    original=x.copy();context=copy.deepcopy(k.context);k.rhs(x,1.4)
    assert np.array_equal(x,original) and context==k.context


@pytest.mark.parametrize('changes',[dict(specified_avr_reference_pu=None),dict(regulating_voltage_bounds_pu=None),dict(regulating_voltage_bounds_pu=(1.,.9)),dict(current_comparison_tolerance_pu=1e-12),dict(model='OEL2C_Dynawo_cap_guard_v1_SEXS_summation'),dict(avr_reference_initialization='unmodified')])
def test_explicit_constraints_and_named_variant_are_required(changes):
    with pytest.raises(ValidationError):passport(**changes)


def test_hold_has_no_artificial_clock_events_or_current_offset(calculated):
    s,r=calculated('hold');assert not r['event_log']
    for key in ['g2.OEL2C.actual_current_pu','g2.OEL2C.pid_integral_pu','g2.SEXS.reference_pu','g2.terminal_voltage_pu']:
        assert np.ptp(r['channels'][key])<1e-9
    assert abs(r['channels']['g2.terminal_voltage_pu'][0]-s.network.generators[1].v_set_pu)>1e-5


def test_specified_reference_changes_control_output_but_not_initial_current_root(calculated):
    s,a=calculated('hold');_,b=calculated('different_reference')
    assert a['channels']['g2.terminal_voltage_pu'][0]==pytest.approx(b['channels']['g2.terminal_voltage_pu'][0],abs=1e-10)
    assert b['channels']['g2.SEXS.reference_pu'][0]==1.14
    assert b['channels']['g2.OEL2C.output_pu'][0]-a['channels']['g2.OEL2C.output_pu'][0]==pytest.approx(-.04,abs=1e-10)


@pytest.mark.parametrize('factor',[.1,7.])
def test_current_root_and_reference_are_network_base_invariant(factor,calculated):
    s,r=calculated('hold');s=s.model_copy(deep=True);s.network.s_base_va*=factor;b=run_study(s);assert b['completed']
    for key in ['g2.terminal_voltage_pu','g2.OEL2C.actual_current_pu','g2.OEL2C.output_pu','g2.SEXS.reference_pu']:
        assert b['channels'][key]==pytest.approx(r['channels'][key],abs=1e-8)


def test_spectral_inventory_and_unstable_poles_cannot_be_hidden(calculated,native_calculated):
    s,r=calculated('hold');b=copy.deepcopy(native_calculated('hold'))
    b['conditional_regulating_spectrum']['eigenvalues_per_s'].pop()
    with pytest.raises(ValueError,match='inventory'):compare_conditional_spectra(r,b)


def test_unstable_gain_is_detected_by_independent_native_spectrum(tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference not configured')
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    from benchmarks.oel2c_model_exchange import run
    s=StudySpec.model_validate(negative('unstable_controller'));s.coupled.duration_s=.02
    model=CoupledNetwork(s.network,s.coupled);state,op=initialize_coupled(model,require_stable=False)
    assert state is not None and op['connected_machine_spectral_abscissa_per_s']>0
    b=run(s,tmp_path/'native',Path(root),1e-11);poles=np.asarray(b['conditional_regulating_spectrum']['eigenvalues_per_s'])
    assert max(poles[:,0])>0


def test_native_current_root_and_initialization_never_use_production_physics(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference not configured')
    from power_grid_env.coupled_generators import RoundRotorBank
    from power_grid_env.coupled_controls import CoupledControllerBank
    from benchmarks.oel2c_model_exchange import run
    def forbidden(*args,**kwargs):raise AssertionError('production physical initialization used')
    for name in ['initial','regulating_equilibrium','rhs','quantities','apply','guards']:monkeypatch.setattr(OEL2CDynamics,name,forbidden)
    for name in ['initialize','field_reaction']:monkeypatch.setattr(RoundRotorBank,name,forbidden)
    monkeypatch.setattr(CoupledControllerBank,'initialize',forbidden)
    s=example('hold');s.coupled.duration_s=.02;b=run(s,tmp_path/'independent',Path(root),1e-11)
    assert b['completed'] and not b['production_limiter_RHS_used']


def test_named_v2_source_change_is_exact_and_v1_is_preserved(tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference not configured')
    import json,shutil
    from benchmarks.dynawo_reference_setup import LOCK,PATCH_FILE,equilibrium_bytes,guarded_bytes,install_equilibrium_copy,verify_equilibrium_copy,verify_guarded_copy
    root=Path(root);lock=json.loads(LOCK.read_text());before={name:(root/'dynawo-cap-guard-v1'/name).read_bytes() for name in lock['files']}
    for name in lock['files']:
        source=root/'dynawo-source'/name;dest=tmp_path/'dynawo-source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        original=source.read_bytes();v2=equilibrium_bytes(name,original)
        assert before[name]==guarded_bytes(name,original)
        if name!=PATCH_FILE:assert v2==original
        else:
            text=v2.decode();assert 'IRefPu >= IInstPu - ICapTolPu' in text
            assert 'timer.u = IActPu > IRefPu + ICmpTolPu;' in text
            assert 'timer1.u = IRefPu > IActPu + IThOffPu + ICmpTolPu;' in text
            assert 'ICmpTolPu' not in before[name].decode()
    install_equilibrium_copy(tmp_path);verify_equilibrium_copy(tmp_path)
    altered=tmp_path/'dynawo-equilibrium-guard-v2'/PATCH_FILE;altered.write_bytes(altered.read_bytes()+b'\n// unauthorized change\n')
    with pytest.raises(ValueError,match='changed equilibrium'):verify_equilibrium_copy(tmp_path)
    verify_guarded_copy(root)
    assert all((root/'dynawo-cap-guard-v1'/n).read_bytes()==v for n,v in before.items())


def test_v2_clock_band_is_explicit_and_legacy_comparators_stay_exact():
    p=passport();k=OEL2CDynamics(p);x=k.initial(1.3,regulating_output_pu=-.08)
    assert not k.context['above'] and not k.context['below']
    x[0]+=p.current_comparison_tolerance_pu/2
    assert k.criteria(x)['above']<0
    from power_grid_env.generator_oel2c import OEL2CPassport
    legacy=OEL2CDynamics(OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.))
    assert legacy.criteria(x)['above']>0
    x[0]+=p.current_comparison_tolerance_pu
    assert k.criteria(x)['above']>0

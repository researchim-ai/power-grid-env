"""Coincident surfaces, conservative sensitivity rejection and compiled witnesses."""
from pathlib import Path
import json,os,copy
from functools import lru_cache
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.generator_oel2c import OEL2CDynamics,OEL2CPassport
from power_grid_env.oel2c_boundary import SensitiveEquilibrium,probe_points
from benchmarks.generator_oel2c_boundary_cases import NAMES,NEGATIVE,example,negative,CENTER,HOT
from benchmarks.generator_oel2c_validation import compare
from benchmarks.oel2c_boundary_component import COMPONENTS

@pytest.fixture(scope='module')
def primary():
    @lru_cache(None)
    def result(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=120.);assert r['completed'],r
        return s,r
    return result

@pytest.mark.parametrize('name',NAMES)
def test_coincident_surfaces_keep_physical_input_and_explicit_scope(name,primary):
    s,r=primary(name);op=r['operating_point'];assert op['checked_dynamic_residual_per_s']<1e-8
    for gid,initial in op['oel2c_selected_equilibrium'].items():
        a=initial['boundary_neighborhood'];assert a['passed'] and a['distinct_effective_modes']==1
        assert a['probes']==len(a['witnesses'])>=5
        assert not a['interval_certified'] and not a['hybrid_stability_qualified'] and not a['physical_input_or_threshold_changed']
        assert sum(row['within_roundoff'] for row in a['central_switching_surfaces'].values())>=2
    json.dumps(op,allow_nan=False)

@pytest.mark.parametrize('name',NAMES)
def test_native_network_independently_agrees_on_benign_initial_branches(name,primary,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference')
    from benchmarks.oel2c_model_exchange import run
    s,r=primary(name);native=run(s,tmp_path/'native',Path(root),1e-12);c=compare(s,r,native)
    assert c['passed'],{k:v for k,v in c['errors'].items() if v>c['limits'][k]}
    for initial in native['limiter_initialization'].values():
        a=initial['boundary_neighborhood'];assert a['passed'] and a['distinct_effective_modes']==1 and not a['interval_certified']

@pytest.mark.parametrize('name',NEGATIVE)
def test_sensitive_or_underspecified_network_start_does_not_return_trace(name):
    try:s=StudySpec.model_validate(negative(name))
    except ValidationError:return
    r=run_study(s,isolate=True,wall_timeout_s=120.);assert not r['completed'] and 'channels' not in r
    if name in NEGATIVE[:7]:
        audit=r['operating_point']['boundary_audit'];assert not audit['passed']
        assert audit['distinct_effective_modes']>1 or any(not w['passed'] for w in audit['witnesses'])
        json.dumps(audit,allow_nan=False)

@pytest.mark.parametrize('name',NEGATIVE[:7])
def test_independent_bracketed_roots_also_reject_sensitive_component(name):
    from benchmarks.oel2c_boundary_reference import check_neighborhood
    s=StudySpec.model_validate(negative(name));p=s.coupled.oel2c_limiters[0];point=HOT if name in ('timer_contact_sensitive','arithmetic_relation_sensitive') else CENTER
    assert not check_neighborhood(p,point)['passed']
    with pytest.raises(SensitiveEquilibrium):OEL2CDynamics(p).initial(point)

@pytest.mark.parametrize('name',COMPONENTS)
def test_native_FMU_derivatives_and_all_channels_at_independent_witnesses(name,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference')
    from benchmarks.oel2c_boundary_component import example,run,compare_primary
    p,point=example(name);r=run(p,point,tmp_path/'native',Path(root));assert compare_primary(p,r)['passed']
    assert r['probes']>=5 and max(v['native_derivative_per_s'] for v in r['witnesses'])<1e-8

@pytest.mark.parametrize('name',['scaled_and_release','hot_plateau','timer_contact_source_sag'])
def test_probe_policy_never_modifies_physical_trajectory(name,primary):
    s,a=primary(name);s=s.model_copy(deep=True)
    for p in s.coupled.oel2c_limiters:p.equilibrium_boundary_policy='native_relations';p.equilibrium_probe_radius_pu=None
    b=run_study(s);assert b['completed']
    for key in ['time_s','sample_side','channels','event_log']:assert a[key]==b[key]

@pytest.mark.parametrize('gain',[.5,1.,2.])
def test_probe_grid_contains_center_structural_root_and_both_sides(gain):
    from benchmarks.oel2c_boundary_component import example
    p,point=example('scaled');p.KScale=gain;point=1/gain
    before=p.model_dump();values=probe_points(p,point);m=np.array(values)*gain
    assert point in values and min(m)<1.<max(m)
    assert np.any(m<1.) and np.any(m>1.) and max(abs(m-1))<=p.equilibrium_probe_radius_pu+1e-15
    x=OEL2CDynamics(p).initial(point);assert p.model_dump()==before

@pytest.mark.parametrize('value',[None,0.,-1.,1e-15,1.,float('nan'),float('inf')])
def test_probe_radius_is_explicit_small_and_finite(value):
    data=example('scaled_and_release').coupled.oel2c_limiters[0].model_dump();data['equilibrium_probe_radius_pu']=value
    with pytest.raises(ValidationError):OEL2CPassport.model_validate(data)


def test_native_witnesses_never_call_production_selectors_or_RHS(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference')
    import power_grid_env.oel2c_stationary as selected
    import power_grid_env.oel2c_boundary as boundary
    from benchmarks.oel2c_boundary_component import example,run
    def forbidden(*a,**kw):raise AssertionError('production implementation used by native witness')
    for module,name in [(selected,'select_stationary'),(selected,'_candidate'),(boundary,'probe_points'),(boundary,'signature')]:monkeypatch.setattr(module,name,forbidden)
    monkeypatch.setattr(OEL2CDynamics,'initial',forbidden);monkeypatch.setattr(OEL2CDynamics,'rhs',forbidden)
    p,point=example('scaled');assert run(p,point,tmp_path/'native',Path(root))['passed']


def test_independent_guard_inventory_is_required(primary,tmp_path):
    from benchmarks.generator_oel2c_selected_validation import initial_audit
    s,r=primary('scaled_and_release');b=copy.deepcopy(r);b['operating_point']['oel2c_selected_equilibrium']={}
    with pytest.raises(ValueError,match='inventory'):initial_audit(s,b,{'AVR_reference_initialization':r['operating_point']['oel2c_AVR_reference_initialization']})

@pytest.mark.parametrize('small_scale',[False,True])
def test_unrepresentable_input_probes_fail_before_nonfinite_report(small_scale):
    from benchmarks.oel2c_boundary_component import example
    from benchmarks.oel2c_boundary_reference import probe_points as native_points
    p,point=example('scaled')
    if small_scale:p.KScale=1e-320;point=0.
    else:point=1e308
    for function in [probe_points,native_points]:
        with pytest.raises(ValueError,match='finite|representable'):function(p,point)


def test_source_arithmetic_difference_can_be_benign_but_is_visible():
    from benchmarks.generator_oel2c_boundary_cases import data
    p=OEL2CPassport(**data('roundoff'));k=OEL2CDynamics(p);x=k.initial(HOT)
    assert (p.tFcl-x[1])>=p.Kzru*p.tFcl
    assert not x[1]<=p.tFcl*(1-p.Kzru)
    audit=k.initialization_audit['boundary_neighborhood'];assert audit['passed'] and audit['source_arithmetic_checked']
    assert any(w['source_thermal_high']!=w['rearranged_thermal_high'] and w['signature']==w['alternative_arithmetic_signature'] for w in audit['witnesses'])


def test_native_relations_follow_source_operation_order_at_release_plateau(tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned native reference')
    import sys
    from benchmarks.dynawo_reference_setup import verify
    from benchmarks.oel2c_model_exchange import NativeBlock
    root=Path(root).resolve();verify(root)
    if str(root/'fmpy') not in sys.path:sys.path.insert(0,str(root/'fmpy'))
    from fmpy.fmi2 import FMU2Model
    values=negative('arithmetic_relation_sensitive')['coupled']['oel2c_limiters'][0]
    values.update(equilibrium_boundary_policy='native_relations',equilibrium_probe_radius_pu=None)
    p=OEL2CPassport.model_validate(values);k=OEL2CDynamics(p);x=k.initial(HOT)
    assert k.context['thermal_high'] and k.quantities(x)['ramp']==.3
    block=NativeBlock(p,HOT,tmp_path/'native',root,.5,1e-12,FMU2Model)
    try:
        reference=block.signals(0.);assert reference['reference_rate_pu_s']==.3
        for key,value in k.signals(x).items():assert reference[key]==pytest.approx(value,abs=1e-8)
        assert block.stationary_initial_residual<1e-8
    finally:block.close()

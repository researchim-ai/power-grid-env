import copy
import numpy as np
import pytest
from benchmarks.generator_replica_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product

@pytest.mark.parametrize('inconsistent',[False,True])
def test_replica_variable_Y_tangent(inconsistent):
    m,x,a=initial(example());u=np.random.default_rng(87).normal(size=len(x));h=2e-6;v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j;system=NetworkDAE(m);f=m.rhs(0,x)
        actual=residual_product(system,0,x,f,v,u,u,dv)
        expected=(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
    assert len(a['physical_eigenvalues_per_s'])==15

@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_replica_independent_fault(method,mode):
    from benchmarks.generator_replica_reference import GeneratorReplicaReference
    from benchmarks.mixed_winding_acceptance import compare
    s=example(mode=mode,fault=True);s['coupled']['integration_method']=method
    r=run_study(StudySpec.model_validate(s));ref=GeneratorReplicaReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']

@pytest.mark.parametrize('kind',['flag','limit','policy'])
def test_replica_refuses_bad_initial_policy(kind):
    s=example()
    if kind=='flag':s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
    elif kind=='limit':s['coupled']['generator_thermal'][0]['trip_level']=.01
    else:s['coupled']['generator_thermal'][0]['initialization']='specified'
    r=run_study(StudySpec.model_validate(s));assert not r['completed']

@pytest.mark.parametrize('method',['radau','network_dae'])
def test_replica_trips_once_and_generator_current_extinguishes(method):
    s=example(mode='passport',fault=True);s['coupled']['integration_method']=method
    h=s['coupled']['generator_thermal'][0];h.update(trip_level=.3,initial_level=.2,time_constant_s=.05)
    r=run_study(StudySpec.model_validate(s));assert r['completed']
    events=[e for e in r['event_log'] if e['kind']=='generator_thermal_trip'];assert len(events)==1
    assert r['channels']['g.connected'][-1]==0. and r['channels']['g.current_pu'][-1]==0.
    assert r['channels']['g.stator_thermal_latched'][-1]==1.

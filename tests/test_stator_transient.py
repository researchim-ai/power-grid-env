"""Full stator flux: native Gamma dynamics, exact locked-rotor matrix and phases."""
import math,os
import numpy as np
import pytest
from scipy.linalg import expm
from pydantic import ValidationError
from power_grid_env.stator_transient import StatorTransientPassport,simulate
from benchmarks.stator_transient_reference import example,reference,compare,NAMES


@pytest.fixture
def external_dependency(monkeypatch):
    dep=os.environ.get('PGE_MOTULATOR_REFERENCE_DIR')
    if dep:monkeypatch.syspath_prepend(dep)
    pytest.importorskip('motulator',reason='optional pinned full-stator native reference')


@pytest.mark.parametrize('name',NAMES)
def test_full_native_stator_and_mechanical_states_match_primary(external_dependency,name):
    p=example(name);p.duration_s=.08
    r=simulate(p);n=reference(p,r['time_s']);assert compare(r,n)['passed']


def test_locked_rotor_with_inductive_supply_matches_exact_six_state_linear_solution():
    p=example('locked_rotor');p.duration_s=.03;p.step_s=.0001;p.source_l_h=.001
    r=simulate(p);assert r['completed']
    inv=np.linalg.inv([[p.stator_self_h+p.source_l_h,p.mutual_h],[p.mutual_h,p.rotor_self_h]])
    K=-np.diag([p.stator_r_ohm+p.source_r_ohm,p.rotor_r_ohm])@inv
    A=np.zeros((6,6))
    for i in range(2):
        for j in range(2):A[2*i,2*j]=A[2*i+1,2*j+1]=K[i,j]
    A[0,4]=A[1,5]=1.;A[4,5]=-2*math.pi*p.frequency_hz;A[5,4]=2*math.pi*p.frequency_hz
    u=p.source_ll_rms_v*math.sqrt(2/3)*np.exp(1j*p.source_phase_rad);x0=np.array([0.,0.,0.,0.,u.real,u.imag])
    expected=[]
    for t in r['time_s']:
        v=expm(A*t)@x0;expected.append((inv@np.array([complex(v[0],v[1]),complex(v[2],v[3])]))[0])
    actual=np.array(r['channels']['current_alpha_a'])+1j*np.array(r['channels']['current_beta_a'])
    assert max(abs(actual-expected))<1e-6
    assert max(abs(np.array(r['channels']['speed_rad_s'])))==0.


def test_energization_preserves_zero_initial_current_and_three_wire_phase_balance():
    p=example('nominal');p.duration_s=.03;r=simulate(p);assert r['completed']
    c=r['channels'];assert c['phase_a_a'][0]==c['phase_b_a'][0]==c['phase_c_a'][0]==0.
    assert max(abs(np.array(c['phase_a_a'])))>1.
    assert max(abs(np.array(c['phase_a_a'])+c['phase_b_a']+c['phase_c_a']))<1e-12
    assert min(c['magnetic_energy_j'])>=-1e-12
    assert not r['general_network_EMT_qualified']


def test_uniform_source_phase_rotation_preserves_torque_and_speed():
    p=example('nominal');p.duration_s=.05;a=simulate(p)
    p.source_phase_rad=.7;b=simulate(p)
    assert b['channels']['torque_nm']==pytest.approx(a['channels']['torque_nm'],abs=1e-6)
    assert b['channels']['speed_rad_s']==pytest.approx(a['channels']['speed_rad_s'],abs=1e-7)


@pytest.mark.parametrize('defect',['inductance','time','frequency','resistance'])
def test_invalid_component_passport_is_rejected(defect):
    data=example('nominal').model_dump()
    if defect=='inductance':data['mutual_h']=data['stator_self_h']*2
    elif defect=='time':data['step_s']=data['duration_s']*2
    elif defect=='frequency':data['frequency_hz']=float('inf')
    else:data['rotor_r_ohm']=-1.
    with pytest.raises(ValidationError):StatorTransientPassport.model_validate(data)


@pytest.mark.parametrize('resource',['maximum_rhs_evaluations','maximum_retained_scalars'])
def test_stator_component_work_and_retained_output_are_bounded(resource):
    p=example('nominal');setattr(p.runtime_limits,resource,1)
    r=simulate(p);assert not r['completed'] and r['status']=='runtime_limit_exceeded'


def test_native_stator_model_never_calls_primary_solver(external_dependency,monkeypatch):
    import benchmarks.stator_transient_reference as v
    monkeypatch.setattr(v,'simulate',lambda *a,**k:pytest.fail('primary solver leaked into native'))
    p=example('nominal');p.duration_s=.01;r=reference(p,[0.,.005,.01])
    assert r['completed'] and r['stator_flux_states_preserved']

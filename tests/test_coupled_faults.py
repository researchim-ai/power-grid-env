"""Finite fault physics, atomic hybrid boundaries and cross-engine checks."""
from pathlib import Path
import copy
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_rms import CoupledFault,CoupledEvent
from benchmarks.coupled_fault_reference import reference,native_controlled_limit

NAMES=['gencls_resistive','genrou_resistive','genrou_reactive','genrou_source','genrou_saturated','genrou_thermal','genrou_overlap']


def example(name='genrou_resistive'):
    return StudySpec.model_validate_json(Path('cases/studies/coupled_fault_'+name+'.json').read_text())


@pytest.mark.parametrize('name',NAMES)
def test_faulted_joint_machine_trajectory_matches_independent_physical_SI_circuit(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    e=reference(s,r)
    for key,values in e['channels'].items():
        tolerance=100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.delta','.omega','.speed_pu')) else 1e-4
        assert max(abs(np.array(values)-r['channels'][key]))<tolerance,key
    trip=next((row['time_s'] for row in r['event_log'] if row['kind']=='thermal_trip'),None)
    assert trip is None if e['trip_time_s'] is None else trip==pytest.approx(e['trip_time_s'],abs=1e-6)
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8
    for f in s.coupled.faults:
        for t,expected in [(f.apply_s,[0.,1.]),(f.clear_s,[1.,0.])]:
            indices=np.flatnonzero(np.asarray(r['time_s'])==t)
            assert len(indices)==2
            assert np.asarray(r['channels'][f.id+'.active'])[indices]==pytest.approx(expected)


@pytest.mark.parametrize('saturated',[False,True])
def test_faulted_generator_only_limit_converges_to_ANDES(saturated):
    r=native_controlled_limit(saturated);assert r['passed'],r
    assert r['errors'][-1]['g.e2q']<r['errors'][0]['g.e2q']


def test_fault_and_motor_events_are_atomic_and_preserve_machine_states():
    s=example();s.coupled.events=[CoupledEvent(time_s=.2,kind='motor_open',equipment_id='m')]
    r=run_study(s);assert r['completed'],r
    pre,post=np.flatnonzero(np.array(r['time_s'])==.2)
    assert [r['sample_side'][pre],r['sample_side'][post]]==['pre_event','post_event']
    for key in ['g.delta','g.omega','g.e1q','g.e1d','g.e2d','g.e2q','m.speed_pu','m.e_real_pu','m.e_imag_pu']:
        assert r['channels'][key][pre]==r['channels'][key][post]
    assert r['channels']['m.connected'][post]==0. and r['channels']['fault.active'][post]==1.
    assert r['channels']['m.current_pu'][post]==0.
    assert r['channels']['m.bus_voltage_pu'][post]<r['channels']['m.bus_voltage_pu'][pre]


def test_faults_sharing_a_bus_add_and_individually_clear():
    s=example();s.coupled.faults.append(CoupledFault(id='second',node_id='plant',apply_s=.25,clear_s=.4,r_ohm=2.,x_ohm=20.))
    r=run_study(s);assert r['completed'],r
    a=np.flatnonzero(np.array(r['time_s'])==.25)[-1]
    assert r['channels']['fault.current_a'][a]/r['channels']['second.current_a'][a]==pytest.approx(2.)
    b=np.flatnonzero(np.array(r['time_s'])==.3)[-1]
    assert r['channels']['fault.current_a'][b]==0. and r['channels']['second.current_a'][b]>0.
    assert r['maximum_power_balance_residual_pu']<1e-8


@pytest.mark.parametrize('group',['Dyn11','YNd1','Dyn5'])
def test_fault_snapshot_on_other_voltage_base_matches_full_ABC(group):
    from test_multivoltage_rms import example as multi
    from test_coupled_rms import example as classical
    from power_grid_env.backend_opendss import solve_network
    s=classical();s.network=multi(group).network;s.network.loads[0].model='constant_z'
    s.coupled.motors[0].node_id='load';s.coupled.duration_s=.05
    s.coupled.faults=[CoupledFault(id='fault',node_id='load',apply_s=.01,clear_s=.03,r_ohm=10.,x_ohm=20.)]
    r=run_study(s);assert r['completed'],r
    index=np.flatnonzero(np.array(r['time_s'])==.01)[-1];c=r['channels'];pu=c['m.bus_voltage_pu'][index]
    power=complex(c['m.p_w'][index]+c['fault.p_w'][index],c['m.q_var'][index]+c['fault.q_var'][index])/pu**2
    for phase in 'ABC':
        s.network.loads[0].p_w[phase]+=power.real/3;s.network.loads[0].q_var[phase]+=power.imag/3
    g=s.network.generators[0];g.mode='pq';g.p_w=c[g.id+'.p_w'][index];g.q_var=c[g.id+'.q_var'][index]
    abc=solve_network(s.network);assert abc.status=='converged',abc.diagnostics
    for v in abc.voltages:
        if v.phase=='A':assert c[v.bus_id+'.v'][index]==pytest.approx(abs(v.complex)/v.v_base_ln_v,abs=1e-5)
    ll=s.coupled.motors[0].rated_ll_v
    assert c['fault.current_a'][index]==pytest.approx(pu*ll/np.sqrt(3)/abs(10+20j))


def test_invalid_faults_reject_before_integration(monkeypatch):
    import power_grid_env.induction_network as solver
    monkeypatch.setattr(solver,'solve_ivp',lambda *a,**k:pytest.fail('must reject before integration'))
    for field,value in [('node_id','missing'),('r_ohm',-1.),('x_ohm',float('inf')),('clear_s',100.),('id','g')]:
        s=example();setattr(s.coupled.faults[0],field,value)
        assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.faults[0].r_ohm=0.;s.coupled.faults[0].x_ohm=0.
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.faults.append(s.coupled.faults[0]);assert run_study(s)['reason']=='coupled_scope'
    for updates in [dict(r_ohm=0.,x_ohm=0.),dict(apply_s=.3,clear_s=.2),dict(clear_s=1.8)]:
        data=example().model_dump(mode='json');data['coupled']['faults'][0].update(updates)
        assert run_study_document(data)['reason']=='schema'


def test_fault_worker_matches_direct_and_deadline(tmp_path):
    s=example('genrou_overlap');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'] and a['channels']==b['channels']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'


def test_refactorization_failure_is_an_explicit_incomplete_result(monkeypatch):
    import power_grid_env.coupled_rms as coupled
    original=coupled.CoupledNetwork.factorize
    def fail_at_fault(self):
        if self.generator_ready and self.active_faults:raise RuntimeError('injected LU failure')
        return original(self)
    monkeypatch.setattr(coupled.CoupledNetwork,'factorize',fail_at_fault)
    r=run_study(example());assert not r['completed'] and r['status']=='network_refactorization_failed'
    assert r['time_s']==.2

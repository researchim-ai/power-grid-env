"""Canonical RMS mapping against independent integration, bases and phase PF."""
import json
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from power_grid_env.build import NetBuilder
from power_grid_env.rms_study import ClassicalMachine, RMSSettings, RMSEvent, validate_rms
from power_grid_env.study_spec import StudySpec, run_study, run_study_document
from power_grid_env.topology import compile_topology


def example(base=100e6, step=.005):
    zbase=110e3**2/100e6
    b=NetBuilder('canonical-rms','Canonical RMS',110.,s_base_va=base)
    b.node('src'); b.node('gen')
    b.source('src',0.,zbase*.1)
    b.line_z1('src','gen',1j*zbase*.2,line_id='line')
    b.generator('gen',50e6,mode='pv',v_set_pu=1.05,q_min_var=-100e6,q_max_var=100e6,generator_id='g')
    return StudySpec(id='canonical-rms', study='rms',backend='andes',network=b.build(),
        rms=RMSSettings(duration_s=.4,step_s=step,machines=[ClassicalMachine(generator_id='g',
        rated_va=100e6,inertia_s=4.,x_d_prime_pu=.3)],events=[RMSEvent(time_s=.1,
        kind='mechanical_step',equipment_id='g',value=.01)]))


def reference(s, report):
    bus=compile_topology(s.network).node_to_bus['gen']
    v=report['initial']['bus_voltage_pu'][bus]*np.exp(1j*report['initial']['bus_angle_rad'][bus])
    e=v+1j*.3*(v-1)/(1j*.3)
    state=[np.angle(e),1.]
    times=np.asarray(report['time_s']); result=np.empty((len(times),2))
    for left,right,pm in [(0.,.1,.5),(.1,.4,.51)]:
        def rhs(time,x):
            return [2*np.pi*50*(x[1]-1),(pm-abs(e)/.6*np.sin(x[0]))/8]
        sol=solve_ivp(rhs,(left,right),state,rtol=1e-11,atol=1e-12,dense_output=True)
        selected=(times>=left)&(times<=right)
        result[selected]=sol.sol(times[selected]).T
        state=sol.y[:,-1]
    return result


def test_imported_classical_machine_matches_independent_rk_and_step_convergence(tmp_path):
    errors=[]
    for step in (.01,.005,.0025):
        s=example(step=step)
        report=run_study_document(json.loads(s.model_dump_json()))
        assert report['completed'],report
        assert report['init_residual']<1e-7
        expected=reference(s,report)
        errors.append(float(np.max(abs(expected[:,0]-report['channels']['g.delta']))))
        assert max(abs(expected[:,1]-report['channels']['g.omega']))<1e-5
        assert report['channels']['g.p_w'][0]==pytest.approx(50e6,rel=1e-8)
        assert report['industrial_profile_qualified'] is False
    assert errors[2]<errors[1]<errors[0]<1e-4
    assert errors[0]/errors[1]==pytest.approx(4.,rel=.2)
    assert errors[1]/errors[2]==pytest.approx(4.,rel=.2)
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(example().model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'result.json')])==0
    assert json.loads((tmp_path/'result.json').read_text())['completed']


def test_machine_rating_and_system_base_are_not_confused():
    a=run_study(example(100e6)); b=run_study(example(50e6))
    assert a['completed'] and b['completed']
    expected=np.interp(a['time_s'],b['time_s'],b['channels']['g.delta'])
    assert np.max(abs(np.array(a['channels']['g.delta'])-expected))<1e-7
    assert b['channels']['g.p_w'][0]==pytest.approx(50e6,rel=1e-8)


def test_two_machines_load_step_and_pf_mapping():
    b=NetBuilder('multi','Multi',110.,s_base_va=100e6)
    for node in ('src','g1','g2'):b.node(node)
    b.source('src',.1,2.)
    b.line_z1('src','g1',.3+8j,line_id='a')
    b.line_z1('g1','g2',.2+5j,line_id='b')
    b.line_z1('src','g2',.2+10j,line_id='c')
    b.generator('g1',30e6,mode='pv',v_set_pu=1.02,q_min_var=-100e6,q_max_var=100e6,generator_id='g1')
    b.generator('g2',20e6,mode='pv',v_set_pu=1.01,q_min_var=-100e6,q_max_var=100e6,generator_id='g2')
    b.load('g2',10e6,2e6,load_id='load')
    net=b.build()
    settings=RMSSettings(duration_s=.3,step_s=.005,machines=[ClassicalMachine(generator_id=g,
        rated_va=100e6,inertia_s=4.,x_d_prime_pu=.3) for g in ('g1','g2')],
        events=[RMSEvent(time_s=.1,kind='load_scale',equipment_id='load',value=2.)])
    report=run_study(StudySpec(id='multi',study='rms',backend='andes',network=net,rms=settings))
    assert report['completed'],report
    assert abs(report['channels']['g1.omega'][-1]-1)>1e-6
    # Independent positive-sequence PV/PQ Newton oracle. This topology is
    # also has a separate cross-backend regression after the PV refinement fix.
    from scipy.optimize import root
    y=np.zeros((4,4),complex)
    for i,j,z in [(0,1,.1+2j),(1,2,.3+8j),(2,3,.2+5j),(1,3,.2+10j)]:
        adm=121./z
        y[i,i]+=adm; y[j,j]+=adm; y[i,j]-=adm; y[j,i]-=adm
    def mismatch(x):
        v=np.array([1.,x[3],1.02,1.01])*np.exp(1j*np.array([0.,*x[:3]]))
        power=v*np.conj(y@v)
        return np.r_[power.real[1:]-[0.,.3,.1],power.imag[1]]
    solved=root(mismatch,[0.,0.,0.,1.],tol=1e-11)
    assert solved.success and max(abs(mismatch(solved.x)))<1e-9
    topology=compile_topology(net)
    for i,node in enumerate(('src','g1','g2')):
        bus=topology.node_to_bus[node]
        expected_v=solved.x[3] if i==0 else [1.02,1.01][i-1]
        assert report['initial']['bus_voltage_pu'][bus]==pytest.approx(expected_v,abs=1e-6)
        assert report['initial']['bus_angle_rad'][bus]==pytest.approx(solved.x[i],abs=1e-6)


def test_scope_rejects_missing_passports_asymmetry_and_islanding_before_backend(monkeypatch):
    s=example()
    import power_grid_env.rms_study as rms
    def forbidden(*args):raise AssertionError('backend must not start')
    monkeypatch.setattr(rms,'build_system',forbidden)
    s.rms.machines=[]
    assert run_study(s)['reason']=='rms_scope'
    s=example();s.network.lines[0].r_ohm[0][0]+=.1
    assert run_study(s)['reason']=='rms_scope'
    s=example();s.rms.events=[RMSEvent(time_s=.1,kind='line_trip',equipment_id='line')]
    assert run_study(s)['reason']=='rms_scope'
    s=example();s.rms.events=[RMSEvent(time_s=.1,kind='load_scale',equipment_id='missing',value=2.)]
    assert run_study(s)['reason']=='rms_scope'
    with pytest.raises(ValueError):RMSSettings(machines=[],duration_s=1,step_s=1e-9)


def test_quiet_machine_keeps_equilibrium_and_trip_removes_its_power():
    s=example();s.rms.events=[]
    quiet=run_study(s)
    assert quiet['completed']
    assert np.max(abs(np.array(quiet['channels']['g.omega'])-1))<1e-9
    assert np.ptp(quiet['channels']['g.delta'])<1e-8
    s.rms.events=[RMSEvent(time_s=.1,kind='generator_trip',equipment_id='g')]
    trip=run_study(s)
    assert trip['completed'],trip
    assert abs(trip['channels']['g.p_w'][-1])<1.
    assert np.all(np.diff(trip['time_s'])>0)


def test_parallel_line_trip_keeps_connected_network_and_changes_voltage():
    s=example()
    duplicate=s.network.lines[0].model_copy(deep=True)
    duplicate.id='parallel'
    # Give the parallel branch distinct terminals, preserving canonical IDs.
    for field in ('terminal_from','terminal_to'):
        old=getattr(duplicate,field)
        t=next(t for t in s.network.terminals if t.id==old).model_copy(deep=True)
        t.id=old+'_parallel';t.equipment_id=duplicate.id
        s.network.terminals.append(t);setattr(duplicate,field,t.id)
    s.network.lines.append(duplicate)
    s.rms.events=[RMSEvent(time_s=.1,kind='line_trip',equipment_id='line')]
    report=run_study(s)
    assert report['completed'],report
    assert abs(report['channels']['g.omega'][-1]-1)>1e-5
    assert all(np.isfinite(values).all() for values in report['channels'].values())

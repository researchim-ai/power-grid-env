import copy
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from benchmarks.three_motor_training import jobs
from benchmarks.three_motor_si_reference import Circuit
from benchmarks.qwen35_train_probe import project,selection
from power_grid_env.network_design import compile_network
from power_grid_env.network_design_view import observation
from power_grid_env.design_multimodal import topology_family,export
from power_grid_env.study_artifacts import verify
from tests.test_design_multimodal import corpus


def study():return compile_network(jobs()[0]['task'],jobs()[0]['proposal'])[2].cases[0].study.model_dump(mode='json')


def test_three_motors_really_share_two_load_sites_and_keep_train_family():
    for j in jobs():
        t,p,plan,c=compile_network(j['task'],j['proposal'])
        assert len(plan.cases)==6
        assert len(t.node_demand_kva)==3
        assert topology_family(observation(t,p))=='a54d530836c2c7926644dd5639658903773175e51b248f9e738202cc601d5b45'
        assert all(len(k.study.induction.motors)==3 for k in plan.cases)
        assert all([m.node_id for m in k.study.induction.motors]==['n1','n2','n2'] for k in plan.cases)
        assert t.node_demand_kva['n2']==150
        for case in plan.cases[3:]:
            events=case.study.induction.events
            assert [e.kind for e in events]==['motor_open','motor_close']
            assert events[0].equipment_id==events[1].equipment_id==case.id.removeprefix('open-reclose-')
        assert c['static']['passes_declared_checks']==j['id'].endswith('star')


def test_independent_nodal_flux_and_torque_match_full_T_circuit_at_fixed_slip():
    spec=study();c=Circuit(spec);wb=c.wb;slip=.12
    nodal=c.y.copy();params=[]
    for m,node in zip(c.motors,c.locations):
        zbase=m['rated_ll_v']**2/m['rated_va']
        rs=m['stator_r_pu']*zbase;rr=m['rotor_r_pu']*zbase
        xm=1j*m['magnetizing_x_pu']*zbase
        rotor=rr/slip+1j*m['rotor_x_pu']*zbase
        branch=1/(1/xm+1/rotor)
        z=rs+1j*m['stator_x_pu']*zbase+branch
        nodal[node,node]+=1/z;params.append((z,branch,rotor,rr))
    rhs=np.zeros(c.n,complex);rhs[c.source]=c.source_y*c.source_v
    volts=np.linalg.solve(nodal,rhs);state=np.zeros((c.k,4));currents=[];torques=[]
    for i,(node,p) in enumerate(zip(c.locations,params)):
        z,branch,rotor,rr=p
        current=volts[node]/z;rotor_current=-current*branch/rotor
        lm=c.motors[i]['magnetizing_x_pu']*(c.motors[i]['rated_ll_v']**2/c.motors[i]['rated_va'])/wb
        lr=(c.motors[i]['rotor_x_pu']+c.motors[i]['magnetizing_x_pu'])*(c.motors[i]['rated_ll_v']**2/c.motors[i]['rated_va'])/wb
        flux=lm*current+lr*rotor_current
        state[i,:3]=flux.real,flux.imag,1-slip
        currents.append(current);torques.append(3*abs(rotor_current)**2*rr/slip/c.motors[i]['rated_va'])
    voltage,current,rotor,torque=c.algebra(state)
    np.testing.assert_allclose(voltage,volts,rtol=1e-12,atol=1e-10)
    np.testing.assert_allclose(current,currents,rtol=1e-12,atol=1e-10)
    np.testing.assert_allclose(torque,torques,rtol=1e-12,atol=1e-10)
    np.testing.assert_allclose(c.rhs(0,state.ravel()).reshape(c.k,4)[:,:2],0,atol=1e-9)


def test_open_motor_coast_matches_closed_form_flux_speed_and_heat():
    c=Circuit(study());c.connected[0]=False;c.rebuild()
    state=np.zeros((c.k,4));state[0]=[.03,-.02,.95,.7]
    sol=solve_ivp(c.rhs,(0,.2),state.ravel(),method='DOP853',rtol=1e-12,atol=1e-13,max_step=.0005)
    m=c.motors[0];t=.2;rate=m['fan_torque_pu']/(2*m['inertia_s'])
    speed=.95/(1+rate*.95*t);integral=np.log1p(rate*.95*t)/rate
    tau=c.params[0,4]/c.params[0,1]
    flux=complex(.03,-.02)*np.exp(-t/tau-1j*c.wb*(t-integral))
    final=sol.y[:,-1].reshape(c.k,4)
    np.testing.assert_allclose(final[0,:3],[flux.real,flux.imag,speed],rtol=1e-9,atol=1e-10)
    assert final[0,3]==pytest.approx(.7*math.exp(-t/m['thermal']['time_constant_s']),abs=1e-10)
    assert abs(c.algebra(final)[1][0])<1e-10


def test_nodal_order_does_not_change_reference_solution():
    spec=study();other=copy.deepcopy(spec);other['network']['connectivity_nodes'].reverse()
    a,b=Circuit(spec),Circuit(other)
    state=np.array([[.03,-.02,.4,.1],[.02,.01,.5,.1],[-.01,.03,.6,.1]])
    np.testing.assert_allclose(a.observe(state.ravel()),b.observe(state.ravel()),atol=1e-8,rtol=1e-11)
    np.testing.assert_allclose(a.rhs(0,state.ravel()),b.rhs(0,state.ravel()),atol=1e-9,rtol=1e-11)


@pytest.mark.parametrize('mode',['image','text','combined','schematic'])
def test_train_projection_removes_answers_but_preserves_public_content(corpus,tmp_path,mode):
    root,sha=corpus;source=tmp_path/'sft';export(root,source,sha,mode=mode)
    from power_grid_env.vlm_batches import VLMExportDataset
    d=VLMExportDataset(source,verify(source))
    prompts=project(source,verify(source),tmp_path/'prompts')
    assert prompts.split=='train' and prompts.kind=='prompts'
    assert prompts.record(0)['messages']==d.record(0)['messages'][:1]
    assert len(prompts.record(0)['messages'])==1


def test_probe_selection_is_first_per_pattern_not_selected_from_predictions():
    import json
    class Data:
        def __len__(self):return 4
        def record(self,i):
            values=[['below_running_speed']*2,['at_running_speed']*2,['at_running_speed','below_running_speed'],['below_running_speed']*2][i]
            return {'messages':[{}, {'content':json.dumps(dict(motors=dict(zip(['m','m2'],values))))}]}
    assert selection(Data())==[('below',0),('mixed',2),('running',1)]

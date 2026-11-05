import math
import numpy as np
import pytest
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionMotor
from power_grid_env.coupled_rms import CoupledSettings,CoupledEvent,CoupledFault
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.network_automation import NetworkAutomation,SynchronismLimits,synchronism
from power_grid_env.study_spec import StudySpec,run_study


def example(*,fault=False):
    b=NetBuilder('network-automation','authored balanced island/relay reference',10.,s_base_va=1e6)
    b.node('grid');b.node('plant');b.source('grid',2.,8.)
    b.line_z1('grid','plant',.2+1j,line_id='feeder');b.load('plant',300000.,100000.,model='constant_z',load_id='load')
    b.generator('plant',300000.,mode='pv',v_set_pu=1.,q_min_var=-1e6,q_max_var=1e6,generator_id='g')
    motor=InductionMotor(id='motor',node_id='plant',rated_va=1e5,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.5,fan_torque_pu=.6,connected=False)
    cfg=CoupledSettings(duration_s=.7,step_s=.005,motors=[motor],machines=[ClassicalMachine(generator_id='g',rated_va=1e6,inertia_s=1.,damping_pu=1.,x_d_prime_pu=.25)],event_policy='coincident_roots_v1',rtol=1e-9,atol=1e-11,
        network_automation=NetworkAutomation.model_validate(dict(breakers=[dict(id='grid-cb',target_kind='source',target_id='source',opening_delay_s=.01),dict(id='line-cb',target_kind='line',target_id='feeder')],relays=[])))
    if fault:cfg.faults=[CoupledFault(id='fault',node_id='plant',apply_s=.1,clear_s=.4,r_ohm=1.,x_ohm=0.)]
    return StudySpec(id='network-automation',study='coupled_rms',backend='scipy',network=b.build(),coupled=cfg)


def test_islanding_and_synchrochecked_source_reconnection_preserve_states():
    s=example();s.coupled.events=[CoupledEvent(time_s=.1,kind='source_open',equipment_id='source'),CoupledEvent(time_s=.4,kind='source_close',equipment_id='source')]
    r=run_study(s);assert r['completed'],r
    applied=[e for e in r['event_log'] if e['kind'] in {'breaker_open','breaker_close'} and e.get('outcome')=='applied']
    assert len(applied)==2 and applied[0]['time_s']==pytest.approx(.11)
    assert applied[1]['synchronism']['allowed']
    assert max(abs(np.array(r['channels']['g.omega'])-1))<1e-7
    for at in [.11,.4]:
        ids=np.flatnonzero(np.isclose(r['time_s'],at,rtol=0,atol=1e-12));assert len(ids)==2
        for key in ['g.delta','g.omega','motor.e_real_pu','motor.e_imag_pu']:assert r['channels'][key][ids[0]]==r['channels'][key][ids[1]]
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8


def test_frequency_difference_blocks_reconnection_even_with_small_angle():
    s=example();s.coupled.events=[CoupledEvent(time_s=.1,kind='source_open',equipment_id='source'),CoupledEvent(time_s=.15,kind='generator_mechanical_step',equipment_id='g',value=.1),CoupledEvent(time_s=.35,kind='source_close',equipment_id='source')]
    r=run_study(s);assert r['completed'],r
    closed=[e for e in r['event_log'] if e['kind']=='breaker_close' and e.get('outcome')=='blocked'];assert closed
    assert closed[0]['synchronism']['reason']=='frequency_difference' and r['channels']['source.connected'][-1]==0


def test_overcurrent_trip_uses_continuous_timer_and_physical_breaker_delay():
    s=example(fault=True);a=s.coupled.network_automation.model_dump();a['relays']=[dict(id='oc',kind='overcurrent',measurement_kind='line',measurement_id='feeder',breaker_id='line-cb',pickup=50.,time_multiplier_s=.02)]
    a['breakers'][1]['opening_delay_s']=.01;s.coupled.network_automation=NetworkAutomation.model_validate(a)
    r=run_study(s);assert r['completed'],r
    trip=next(e for e in r['event_log'] if e['kind']=='network_relay_trip');opened=next(e for e in r['event_log'] if e['kind']=='breaker_open' and e.get('outcome')=='applied')
    assert trip['time_s']==pytest.approx(.12,abs=1e-8) and opened['time_s']==pytest.approx(.13,abs=1e-8)
    assert r['channels']['line-cb.closed'][-1]==0


@pytest.mark.parametrize('defect',['voltage','angle','frequency','invalid_frequency','incoming_dead'])
def test_synchronism_fail_closed(defect):
    left=right=1+0j;lf=rf=50.
    if defect=='voltage':left=1.2+0j
    elif defect=='angle':left=np.exp(.3j)
    elif defect=='frequency':lf=50.2
    elif defect=='invalid_frequency':lf=None
    else:left=0j
    r=synchronism(left,right,lf,rf,SynchronismLimits());assert not r['allowed']


def test_breaker_failure_preserves_failed_position_and_trips_backup():
    s=example(fault=True);d=s.coupled.network_automation.model_dump()
    d['breakers'][1].update(fail_open=True,opening_delay_s=.01,breaker_failure_delay_s=.02,backup_breaker_ids=['grid-cb'])
    d['relays']=[dict(id='oc',kind='overcurrent',measurement_kind='line',measurement_id='feeder',breaker_id='line-cb',pickup=50.,time_multiplier_s=.02)]
    s.coupled.network_automation=NetworkAutomation.model_validate(d);r=run_study(s);assert r['completed'],r
    e=r['event_log'];failed=next(x for x in e if x['kind']=='breaker_open' and x.get('outcome')=='failed_to_operate')
    confirmed=next(x for x in e if x['kind']=='breaker_failure_confirmed');backup=next(x for x in e if x['kind']=='breaker_open' and x['equipment_id']=='grid-cb' and x.get('outcome')=='applied')
    assert failed['time_s']==pytest.approx(.13,abs=1e-8) and confirmed['time_s']==pytest.approx(.15,abs=1e-8) and backup['time_s']==pytest.approx(.16,abs=1e-8)
    assert r['channels']['line-cb.closed'][-1]==1 and r['channels']['grid-cb.closed'][-1]==0


def test_manual_open_inhibits_previously_queued_automatic_reclose():
    s=example(fault=True);d=s.coupled.network_automation.model_dump();d['breakers'][1].update(maximum_reclose_shots=1,reclose_dead_s=.1)
    d['relays']=[dict(id='oc',kind='overcurrent',measurement_kind='line',measurement_id='feeder',breaker_id='line-cb',pickup=50.,time_multiplier_s=.02)]
    s.coupled.network_automation=NetworkAutomation.model_validate(d);s.coupled.events=[CoupledEvent(time_s=.18,kind='breaker_open',equipment_id='line-cb')]
    r=run_study(s);assert r['completed'],r
    assert any(e.get('reason')=='manual_inhibit' for e in r['event_log'])
    assert r['channels']['line-cb.closed'][-1]==0 and r['channels']['line-cb.manual_inhibit'][-1]==1


@pytest.mark.parametrize('failed_primary',[False,True])
def test_standby_generator_transfer_requires_actual_primary_open(failed_primary):
    s=example();s.network.generators[0].p_w=0.
    d=s.coupled.network_automation.model_dump();d['breakers'][0]['fail_open']=failed_primary
    d['breakers'].append(dict(id='reserve-cb',target_kind='generator',target_id='g',initial_open=True,closing_delay_s=.01))
    d['transfers']=[dict(id='ats',primary_breaker_id='grid-cb',reserve_breaker_id='reserve-cb',primary_voltage=dict(kind='source',id='source'),reserve_voltage=dict(kind='generator',id='g'),detection_delay_s=.02,dead_time_s=.02)]
    s.coupled.network_automation=NetworkAutomation.model_validate(d)
    s.coupled.events=[CoupledEvent(time_s=.1,kind='source_voltage',equipment_id='source',value=0.),CoupledEvent(time_s=.4,kind='source_voltage',equipment_id='source',value=1.),CoupledEvent(time_s=.5,kind='source_close',equipment_id='source')]
    r=run_study(s);assert r['completed'],r
    if failed_primary:
        assert any(e['kind']=='transfer_blocked' and e['reason']=='primary_not_open' for e in r['event_log'])
        assert r['channels']['reserve-cb.closed'][-1]==0
    else:
        close=next(e for e in r['event_log'] if e['kind']=='breaker_close' and e['equipment_id']=='reserve-cb' and e.get('outcome')=='applied')
        assert close['time_s']==pytest.approx(.16,abs=1e-8) and close['synchronism']['reason']=='dead_receiver'
        assert any(e['kind']=='breaker_close' and e['equipment_id']=='grid-cb' and e.get('synchronism',{}).get('reason')=='interlocked_breaker_closed' for e in r['event_log'])
        assert r['channels']['reserve-cb.closed'][-1]==1


def test_measurement_loss_blocks_trip_without_fabricating_zero_current_validity():
    s=example(fault=True);d=s.coupled.network_automation.model_dump();d['relays']=[dict(id='oc',kind='overcurrent',measurement_kind='line',measurement_id='feeder',breaker_id='line-cb',pickup=50.,time_multiplier_s=.02)]
    s.coupled.network_automation=NetworkAutomation.model_validate(d)
    s.coupled.events=[CoupledEvent(time_s=.09,kind='relay_measurement_lost',equipment_id='oc')]
    r=run_study(s);assert r['completed'],r
    assert not any(e['kind']=='network_relay_trip' for e in r['event_log'])
    assert r['channels']['oc.measurement_valid'][-1]==0


def test_equipment_and_relay_order_does_not_change_actions_or_trajectory():
    from benchmarks.network_automation_reference import example as make
    first=make('breaker_failure');other=first.model_copy(deep=True)
    other.coupled.network_automation.breakers.reverse();other.coupled.network_automation.relays.reverse()
    a=run_study(first);b=run_study(other);assert a['completed'] and b['completed']
    assert a['time_s']==b['time_s'] and a['event_log']==b['event_log']
    for key in a['channels']:assert a['channels'][key]==pytest.approx(b['channels'][key],abs=1e-12)


def test_zero_reference_solver_retains_physical_KCL_and_rejects_hidden_injection():
    from scipy.sparse import csc_matrix
    from power_grid_env.network_automation import DeenergizedNodalSolve
    matrix=csc_matrix(np.array([[2.,0.,0.],[0.,1.,-1.],[0.,-1.,1.]],complex))
    solver=DeenergizedNodalSolve(matrix,[1,2]);rhs=np.array([4.,0.,0.],complex);v=solver.solve(rhs)
    assert np.array_equal(matrix@v,rhs) and np.array_equal(v,[2.,0.,0.])
    with pytest.raises(ValueError,match='nonzero injection'):solver.solve(np.array([4.,1.,0.]))


@pytest.mark.parametrize('directional',[False,True])
def test_reverse_power_blocks_directional_overcurrent_only(directional):
    s=example();s.network.generators[0].p_w=600000.;s.coupled.duration_s=.08
    d=s.coupled.network_automation.model_dump();d['relays']=[dict(id='oc',kind='overcurrent',measurement_kind='source',measurement_id='source',breaker_id='grid-cb',pickup=.1,time_multiplier_s=.02,directional=directional)]
    s.coupled.network_automation=NetworkAutomation.model_validate(d);r=run_study(s);assert r['completed'],r
    tripped=any(e['kind']=='network_relay_trip' for e in r['event_log'])
    assert tripped is (not directional)


def test_transfer_rejects_mismatched_supply_measurement():
    s=example();d=s.coupled.network_automation.model_dump()
    d['breakers'].append(dict(id='reserve-cb',target_kind='generator',target_id='g'))
    d['transfers']=[dict(id='ats',primary_breaker_id='grid-cb',reserve_breaker_id='reserve-cb',primary_voltage=dict(kind='generator',id='g'),reserve_voltage=dict(kind='generator',id='g'))]
    s.coupled.network_automation=NetworkAutomation.model_validate(d)
    r=run_study(s);assert not r['completed'] and 'transfer supply voltage' in str(r)

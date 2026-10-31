"""Independent control decisions, physical SI trajectories and commanded ANDES."""
import pytest
from power_grid_env.study_spec import run_study
from benchmarks.automatic_coupled_motor import example,compare,NAMES
from benchmarks.genrou_coupled_reference import reference
from benchmarks.independent_contactor import run as independent_hybrid


@pytest.mark.parametrize('name',NAMES)
def test_independent_si_closed_loop_matches_all_decisions_and_control_channels(name):
    r=compare(example(name));assert r['passed'],r
    assert r['events_match'] and r['maximum_event_time_error_s']<=1e-6
    assert not r['field_validated'] and not r['industrial_profile_qualified']
    actions=[(e['kind'],e['time_s']) for e in r['expected_events'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]
    known={'basic':[('motor_open',.28),('motor_close',.62)],'short_sag':[],
        'hysteresis':[('motor_open',.28),('motor_close',.62)],'minimum_off':[('motor_open',.28),('motor_close',.98)],
        'manual_inhibit':[('motor_open',.28)],'deadline_recovery':[],
        'retry_limit':[('motor_open',.28),('motor_close',.62),('motor_open',.88)],
        'pickup_deadline_loss':[('motor_open',.28),('motor_close',1.02)],'initially_open':[],'manual_close_loss':[]}
    if name in known:
        assert [k for k,t in actions]==[k for k,t in known[name]]
        assert [t for k,t in actions]==pytest.approx([t for k,t in known[name]],abs=1e-12)
    if name in {'thermal_trip','thermal_before_dropout'}:
        assert not any(e.get('cause')=='automatic_restart' for e in r['expected_events'])
        assert any(e.get('outcome')=='blocked_thermal_latch' for e in r['expected_events'])
        assert any(e['kind']=='thermal_trip' for e in r['expected_events'])
    if name=='thermal_before_dropout':assert next(e['time_s'] for e in r['expected_events'] if e['kind']=='thermal_trip')<.28
    if name=='thermal_after_manual_close':
        assert any(e['kind']=='motor_close' and e['time_s']==.6 and e.get('outcome')=='applied' for e in r['expected_events'])
        assert any(e['kind']=='motor_close' and e['time_s']==.8 and e.get('outcome')=='blocked_thermal_latch' for e in r['expected_events'])
    if name=='continuous_voltage':assert any(e['kind']=='contactor_voltage_available' and e['time_s'] not in [x.time_s for x in example(name).coupled.events] for e in r['expected_events'])


def test_reference_never_calls_production_controller_or_simulation(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('production controller/simulator called by oracle')
    monkeypatch.setattr('power_grid_env.motor_contactor.MotorContactorBank',forbidden)
    monkeypatch.setattr('benchmarks.genrou_coupled_reference.run_study',forbidden)
    sample=dict(time_s=[0.,.2,.2,.28,.28,.5,.5,.62,.62,1.2],sample_side=['initial','pre_event','post_event','pre_event','post_event','pre_event','post_event','pre_event','post_event','regular'])
    n=reference(example('basic'),sample,hybrid_runner=independent_hybrid)
    assert [(e['kind'],e['time_s']) for e in n['independent_event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]==[('motor_open',.28),('motor_close',.62)]
    assert n['channels']['m.contactor_restart_count'][-1]==1


@pytest.mark.parametrize('name',['basic','continuous_voltage'])
def test_control_reference_is_independent_of_coarse_output_grid(name):
    s=example(name);s.coupled.step_s=.037;r=compare(s);assert r['passed'],r


def test_independent_transition_budget_exhaustion_is_failure():
    s=example('basic');s.coupled.maximum_contactor_transitions=1
    r=run_study(s);assert not r['completed'] and r['status']=='contactor_control_failed'
    with pytest.raises(ValueError,match='transition budget'):
        reference(s,dict(time_s=[0.,1.2],sample_side=['initial','regular']),hybrid_runner=independent_hybrid)


def test_independent_unknown_sensing_node_is_not_silently_substituted():
    s=example('basic');s.coupled.motors[0].contactor.sense_node_id='missing'
    with pytest.raises(ValueError,match='sensing node'):reference(s,{},hybrid_runner=independent_hybrid)


def test_independent_event_target_is_checked():
    s=example('basic');s.coupled.events[0].equipment_id='not-source'
    with pytest.raises(ValueError,match='target mismatch'):reference(s,{},hybrid_runner=independent_hybrid)


@pytest.mark.parametrize('name',['basic','continuous_voltage','thermal_trip','pickup_deadline_loss'])
def test_native_physics_under_independent_control_commands(name,tmp_path):
    from benchmarks.automatic_motor_native import compare as native_compare
    r=native_compare(example(name),steps=(.00025,.0000625),out=tmp_path/name)
    assert r['passed'] and r['convergence_passed'],r
    assert not r['native_closed_loop_controller_qualified'] and r['adapted_native_physics_under_independent_commands']
    for step in r['steps']:
        assert step['native_honest_jacobian']
        assert step['maximum_accepted_algebraic_residual_pu']<=1e-8


def test_command_oracle_removes_blocked_close_and_marks_only_sampling_instant():
    from benchmarks.automatic_motor_native import command_oracle
    s=example('thermal_trip')
    events=[dict(time_s=.1,kind='thermal_trip',equipment_id='m'),dict(time_s=.6,kind='motor_close',equipment_id='m',outcome='blocked_thermal_latch')]
    oracle,p=command_oracle(s,events)
    assert oracle.coupled.motors[0].contactor is None and oracle.coupled.motors[0].thermal is None
    assert not any(e.kind=='motor_close' for e in oracle.coupled.events)
    assert any(e.kind=='motor_open' and e.time_s==.1 for e in oracle.coupled.events)
    assert p['noop_source_voltage_sampling_markers_s']==[.6] and not p['production_decisions_used']
    assert next(e for e in oracle.coupled.events if e.time_s==.6).value==1.


def test_event_pair_alignment_is_bounded_and_cannot_shift_regular_clock():
    from benchmarks.automatic_motor_native import event_alignment
    r=dict(time_s=[0.,.2,.2,.5,.5,1.2],sample_side=['initial','pre_event','post_event','pre_event','post_event','regular'])
    assert event_alignment(r,[.2+1e-8,.5])=={.2:.2+1e-8,.5:.5}
    with pytest.raises(ValueError,match='time mismatch'):event_alignment(r,[.2+2e-6,.5])
    with pytest.raises(ValueError,match='count mismatch'):event_alignment(r,[.2])


def test_native_worker_failure_is_not_a_physics_pass(monkeypatch):
    from benchmarks.automatic_motor_native import compare as native_compare
    monkeypatch.setattr('benchmarks.automatic_motor_native.native_reference',lambda *a,**k:dict(completed=False,status='native_worker_failed'))
    r=native_compare(example('basic'),steps=(.00025,.0000625));assert not r['passed'] and not r['convergence_passed']


@pytest.mark.parametrize('change',['line_asymmetry','voltage_base'])
def test_independent_hybrid_rejects_unrepresented_circuit(change):
    s=example('basic')
    if change=='line_asymmetry':s.network.lines[0].r_ohm[1][1]+=.1
    else:s.network.generators[0].ll_v=11000.
    with pytest.raises(ValueError):reference(s,{},hybrid_runner=independent_hybrid)

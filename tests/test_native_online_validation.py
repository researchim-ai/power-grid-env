"""Acceptance must reject changed limits, missing events and bad native runs."""
import copy
import pytest
from benchmarks.native_online_validation import STEPS, align_event_sides, match_events, qualify, normalized_error


def rows():
    return [dict(completed=True,step_s=h,errors={'production:m.speed_pu':e,'SI:m.speed_pu':e,'production:m.connected':0.},
                 limits={'production:m.speed_pu':1e-5,'SI:m.speed_pu':1e-5,'production:m.connected':0.},maximum_event_time_error_s=t)
            for h,e,t in zip(STEPS,(4e-5,1e-5,2e-6),(4e-6,1e-6,2e-7))]


def test_finest_limits_apply_without_hiding_coarse_errors():
    result=qualify(rows())
    assert result['passed'] and result['maximum_normalized_errors_by_step']==pytest.approx([4.,1.,.2])


@pytest.mark.parametrize('fault',['discrete','event','nonconvergent','incomplete'])
def test_unacceptable_results_never_pass(fault):
    data=rows()
    if fault=='discrete':data[-1]['errors']['production:m.connected']=1.
    elif fault=='event':data[-1]['maximum_event_time_error_s']=2e-6
    elif fault=='nonconvergent':data[0]['errors']['SI:m.speed_pu']=1e-7
    else:data[-1]={'step_s':STEPS[-1],'completed':False}
    assert not qualify(data)['passed']


@pytest.mark.parametrize('fault',['missing_step','limit_drift','missing_channel','nan','negative_time'])
def test_malformed_refinement_is_rejected(fault):
    data=rows()
    if fault=='missing_step':data.pop()
    elif fault=='limit_drift':data[-1]['limits']['production:m.speed_pu']=1.
    elif fault=='missing_channel':del data[-1]['errors']['SI:m.speed_pu']
    elif fault=='nan':data[-1]['errors']['SI:m.speed_pu']=float('nan')
    else:data[-1]['maximum_event_time_error_s']=-1.
    with pytest.raises(ValueError):qualify(data)


def timeline(a,b):
    return dict(time_s=[0.,a,a,b,b,1.],sample_side=['regular','pre_event','post_event','pre_event','post_event','regular'])


def test_event_alignment_preserves_nearby_events_and_regular_clock():
    reference=timeline(.2,.2000000005);native=timeline(.20000001,.2000000105)
    aligned,delta=align_event_sides(reference,native)
    assert aligned==native['time_s'] and aligned[1]<aligned[3]
    assert delta==pytest.approx(1e-8)


def test_production_initial_sample_matches_native_regular_origin():
    reference=timeline(.2,.5);native=copy.deepcopy(reference)
    reference['sample_side'][0]='initial'
    assert align_event_sides(reference,native)[0]==native['time_s']
    native['sample_side'][-1]='initial'
    with pytest.raises(ValueError,match='initial'):align_event_sides(reference,native)


@pytest.mark.parametrize('fault',['missing_side','unknown_side','horizon','reordered','cross_regular'])
def test_invalid_event_alignment_is_rejected(fault):
    ref=timeline(.2,.5);native=copy.deepcopy(ref)
    if fault=='missing_side':native['sample_side'][2]='regular'
    elif fault=='unknown_side':native['sample_side'][0]='unknown'
    elif fault=='horizon':native['time_s'][-1]=2.
    elif fault=='reordered':native['time_s'][3:5]=[.1,.1]
    else:
        ref['time_s'].insert(3,.3);ref['sample_side'].insert(3,'regular')
        native['time_s'][1:3]=[.4,.4]
    with pytest.raises(ValueError):align_event_sides(ref,native)


def test_event_order_and_blocked_action_outcomes_are_checked():
    events=[dict(time_s=.2,kind='motor_close',equipment_id='m',outcome='blocked_thermal_latch')]
    changed=copy.deepcopy(events);changed[0]['time_s']+=1e-7
    assert match_events(events,changed)==pytest.approx(1e-7)
    changed[0]['outcome']='applied'
    with pytest.raises(ValueError):match_events(events,changed)
    with pytest.raises(ValueError):match_events(events,[])


def test_discrete_mismatch_has_no_finite_normalized_score():
    assert normalized_error({'x':0.},{'x':0.})==0.
    assert normalized_error({'x':1.},{'x':0.})==float('inf')


@pytest.mark.parametrize('fault',['missing_native','missing_SI','nan_reference','bad_residual','nan_residual','failed_worker'])
def test_trace_comparison_fails_closed_on_corrupted_evidence(fault,monkeypatch):
    from benchmarks import native_online_validation as validation
    spec=validation.load_case('grid_staggered');limits=validation.channel_limits(spec)
    native=dict(completed=True,step_s=STEPS[-1],time_s=[0.,1.2],sample_side=['regular','regular'],
                channels={k:[0.,0.] for k in limits},maximum_accepted_algebraic_residual_pu=0.,
                native_driver_metadata=dict(event_log=[],root_trial_steps=1,rollback_count=0))
    production=dict(time_s=[0.,1.2],sample_side=['initial','regular'],channels=copy.deepcopy(native['channels']),event_log=[])
    independent=dict(channels=copy.deepcopy(native['channels']),independent_event_log=[])
    monkeypatch.setattr(validation,'reconstruct_terminals',lambda *a,**kw:dict(channels={'source.p_w':[0.,0.],'source.q_var':[0.,0.]}))
    if fault=='missing_native':del native['channels']['m.speed_pu']
    elif fault=='missing_SI':del independent['channels']['m.speed_pu']
    elif fault=='nan_reference':production['channels']['m.speed_pu'][0]=float('nan')
    elif fault=='bad_residual':native['maximum_accepted_algebraic_residual_pu']=1e-5
    elif fault=='nan_residual':native['maximum_accepted_algebraic_residual_pu']=float('nan')
    else:
        native['completed']=False
        assert not validation.compare_trace(spec,native,production,independent)['completed']
        return
    with pytest.raises(ValueError):validation.compare_trace(spec,native,production,independent)

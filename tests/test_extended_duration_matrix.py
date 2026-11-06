import pytest
from benchmarks.extended_duration_matrix import NAMES,case
from power_grid_env.study_spec import StudySpec
from power_grid_env.coupled_rms import validate_coupled
from power_grid_env.induction_network import validate_induction


@pytest.mark.parametrize('name',NAMES)
def test_long_mesh_matrix_is_valid_and_has_repeated_disturbances(name):
    s=case(name);p=s.coupled or s.induction
    assert StudySpec.model_validate_json(s.model_dump_json())==s
    check=validate_coupled(s.network,p) if s.coupled else validate_induction(s.network,p)
    assert check['ok'],check
    assert p.duration_s>=12.
    assert len(s.network.lines)+len(s.network.transformers)>=len(s.network.connectivity_nodes)
    assert len([e for e in p.events if e.kind=='source_voltage'])>=2


def test_unknown_duration_case_rejected():
    with pytest.raises(ValueError):case('unbounded')


@pytest.mark.parametrize('policy',['legacy','coincident_roots_v1'])
def test_independent_reference_honors_declared_command_order(policy):
    import numpy as np
    from benchmarks.rms_scalability import example
    from power_grid_env.induction import InductionEvent
    from benchmarks.independent_group_contactor import run
    s=example(4).induction;s.duration_s=.03;s.step_s=.01;s.event_policy=policy
    base=s.motors[0];s.motors=[base.model_copy(deep=True,update={'id':'m'+str(i),'connected':False}) for i in range(12)]
    s.events=[InductionEvent(time_s=.01,kind='motor_close',equipment_id=m.id) for m in s.motors]
    cfg=dict(connected=np.zeros(12,dtype=bool),latched=np.zeros(12,dtype=bool),scale=1.,load_scale=np.ones(12))
    segments,changes,trips,events=run(settings=s,state=np.zeros(1),rhs=lambda t,x:np.zeros(1),
        voltage=lambda x,i:1.,heating=lambda x:np.zeros(12),thermal_indices={},
        get_context=lambda:cfg,set_context=lambda c:None,maximum_step_s=.01)
    ids=[e['equipment_id'] for e in events['event_log']]
    expected=[m.id for m in s.motors]
    assert ids==(sorted(expected) if policy=='coincident_roots_v1' else expected)


def test_reference_does_not_snap_flat_nearby_limiter_by_state_epsilon():
    import numpy as np
    from types import SimpleNamespace
    from benchmarks.controller_reference_bounds import attach_layout
    circuit=SimpleNamespace(s=SimpleNamespace(event_policy='coincident_roots_v1'),cfg={},rhs=lambda t,x:np.ones(2)*1e-8)
    entries=[(i,'g'+str(i),'SEXS',0.,1.,lambda x:1e-8) for i in range(2)]
    attach_layout(circuit,entries)
    state=np.array([1.-1e-13,1.]);events=circuit.extra_apply({(1,'upper')},.1,state,circuit.cfg)
    assert [e['equipment_id'] for e in events]==['g1']
    assert circuit.cfg['reference_controller_modes'].tolist()==[0,1]


@pytest.mark.parametrize('separation',[0.,2e-5])
def test_reference_locates_flat_guard_cohorts_in_time(separation):
    import numpy as np
    from benchmarks.rms_scalability import example
    from benchmarks.independent_group_contactor import run
    s=example(4).induction;s.duration_s=.03;s.step_s=.01;s.event_policy='coincident_roots_v1'
    cfg=dict(connected=np.ones(1,dtype=bool),latched=np.zeros(1,dtype=bool),scale=1.,load_scale=np.ones(1));fired=set()
    def guards(state):
        out=[]
        for i,root in enumerate((.01,.01+separation)):
            if i in fired:continue
            def guard(t,x,root=root):return 1e-8*(t-root)
            guard.terminal=True;guard.direction=1;out.append((i,guard))
        return out
    def apply(roots,t,x,context):
        fired.update(roots)
        return [dict(kind='guard',time_s=t,equipment_id=str(i)) for i in sorted(roots)]
    segments,changes,trips,events=run(settings=s,state=np.zeros(1),rhs=lambda t,x:np.zeros(1),voltage=lambda x,i:1.,
        heating=lambda x:np.zeros(1),thermal_indices={},get_context=lambda:cfg,set_context=lambda c:None,
        extra_guards=guards,extra_apply=apply,maximum_step_s=.01)
    assert fired=={0,1}
    assert changes==pytest.approx([.01] if separation==0 else [.01,.01+separation],abs=1e-14)


def test_unheld_reference_limiter_uses_free_rate_during_root_search():
    import numpy as np
    from types import SimpleNamespace
    from benchmarks.controller_reference_bounds import attach_layout
    c=SimpleNamespace(s=SimpleNamespace(event_policy='coincident_roots_v1'),cfg={},rhs=lambda t,x:np.zeros(1))
    attach_layout(c,[(0,'g','SEXS',0.,1.,lambda x:2.)])
    assert c.rhs(0.,np.array([1.00001]))[0]==2.
    x=np.array([1.]);c.extra_apply({(0,'upper')},.1,x,c.cfg)
    assert c.rhs(.1,x)[0]==0.


@pytest.mark.parametrize('group',['gov','avr'])
def test_primary_unheld_trial_rate_and_jacobian_remain_free(group):
    from benchmarks.genrou_jacobian_validation import case
    from tests.test_esst1a_jacobian import ready
    import numpy as np
    s=case('genrou_limits_recovery');s.coupled.event_policy='coincident_roots_v1';m,x=ready(s)
    if group=='gov':
        i=next(iter(m.controllers.gov));address=m.controllers.gov[i][0];p=m.gen_passports[i].governor
        x[address]=p.VMAX+.001;m.controllers.pref[i]+=10;expected=-1/p.T1
    else:
        i=next(iter(m.controllers.avr));address=m.controllers.avr[i][1];p=m.gen_passports[i].exciter
        x[address]=p.EMAX+.001;m.controllers.vref[i]+=10;expected=-1/p.TE
    assert m.rhs(0.,x)[address]>0
    callback=m.integration_jacobian();jac=callback(0.,x)
    h=1e-6;plus=x.copy();minus=x.copy();plus[address]+=h;minus[address]-=h
    numerical=(m.rhs(0.,plus)[address]-m.rhs(0.,minus)[address])/(2*h)
    assert jac[address,address]==pytest.approx(expected)
    assert numerical==pytest.approx(expected,rel=1e-6)
    m.controller_boundaries.active[address]='upper'
    assert m.rhs(0.,x)[address]==0
    assert np.max(abs(callback(0.,x)[address]))==0


def test_commuting_alignment_keeps_existing_clock_limit_and_device_order():
    from benchmarks.controller_reference_bounds import align_commuting_groups
    def event(id_,t,kind='controller_limit_enter',bound='upper'):
        return dict(equipment_id=id_,controller='SEXS',time_s=t,kind=kind,bound=bound)
    a=[event('g0',.1),event('g1',.1+1e-13)];b=[event('g0',.1),event('g1',.1)]
    mapping,audit=align_commuting_groups([.1,.1+1e-13],[.1],a,b,[])
    assert len(set(mapping.values()))==1 and audit['maximum_controller_event_error_s']<1e-12
    with pytest.raises(ValueError,match='time'):
        align_commuting_groups([.1,.100019],[.1],[event('g0',.1),event('g1',.100019)],b,[])
    with pytest.raises(ValueError,match='scheduled'):
        align_commuting_groups([.1,.1+1e-13],[.1],a,b,[.1])
    with pytest.raises(ValueError,match='sequence'):
        align_commuting_groups([.1],[.1],[event('g0',.1)],[event('g0',.1,'controller_limit_release','release')],[])


def test_nonfinite_alignment_clock_rejected():
    from benchmarks.controller_reference_bounds import align_commuting_groups
    with pytest.raises(ValueError,match='finite'):align_commuting_groups([float('nan')],[0.],[],[],[])

"""Per-FMU event causes, strict causal qualification and metamorphic invariance."""
from pathlib import Path
from functools import lru_cache
import itertools,json,math,os
import numpy as np
import pytest
from benchmarks.esst1a_network import uel_event_reasons
from benchmarks.generator_esst1a_refinement import case,reset_distance
from benchmarks.generator_esst1a_joint_validation import compare,coalesce_zero_time_batches
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.coupled_rms import CoupledEvent


@pytest.mark.parametrize('policy',['causal','broadcast'])
@pytest.mark.parametrize('root,changed,timed,step',list(itertools.product([False,True],repeat=4)))
def test_only_own_causes_or_input_discontinuity_request_event_iteration(policy,root,changed,timed,step):
    before=[.1,.2,1.];after=[.1,.3 if changed else .2,1.]
    reasons=uel_event_reasons(policy,before,after,root,timed,step)
    assert bool(reasons)==(policy=='broadcast' or root or changed or timed or step)
    assert ('input_discontinuity' in reasons)==changed
    assert ('own_state_event' in reasons)==root
    assert ('own_time_event' in reasons)==timed
    assert ('own_step_event' in reasons)==step


def test_invalid_event_policy_creates_no_native_artifacts(tmp_path):
    from benchmarks.esst1a_network import run
    with pytest.raises(ValueError,match='event policy'):run(None,tmp_path/'out',tmp_path/'missing',uel_event_policy='unknown')
    assert not (tmp_path/'out').exists()


@pytest.fixture
def fake_guard():
    from benchmarks.mnlex2_model_exchange import NativeUEL,INPUTS
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    b=NativeUEL.__new__(NativeUEL);b.nx=2;b.native_state_names=['memory','limiter.simpleLagLim.state'];b.p=UnderexcitationLimiter(generator_id='g',K_M=2.)
    b.real={k:i for i,k in enumerate([*INPUTS,'limiter.feedback.y'])}
    class FMU:
        raw=-.02;feedback=-.009;rate=-.25
        def getContinuousStates(self,ptr,n):np.ctypeslib.as_array(ptr,(n,))[:]=[0.,self.raw]
        def getDerivatives(self,ptr,n):np.ctypeslib.as_array(ptr,(n,))[:]=[0.,self.rate]
        def getReal(self,refs):return [[.1,.2,1.,self.feedback][i] for i in refs]
    b.f=FMU();return b


def test_guard_audit_keeps_source_operands_separate_from_expanded_ODE(fake_guard):
    s=fake_guard.guard_snapshot()
    assert s['source_guard_numerator_pu']>0 and s['native_ode_rate_pu_per_s']<0
    assert s['strict_lower_condition'] and not s['strict_upper_condition']
    assert s['native_inputs']==[.1,.2,1.]
    assert fake_guard.f.raw==-.02


@pytest.mark.parametrize('field,value',[('raw',float('nan')),('feedback',float('inf')),('rate',float('nan'))])
def test_nonfinite_guard_audit_is_rejected(fake_guard,field,value):
    setattr(fake_guard.f,field,value)
    with pytest.raises(ValueError,match='nonfinite'):fake_guard.guard_snapshot()


@pytest.fixture(scope='module')
def native_pair(tmp_path_factory):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    from benchmarks.esst1a_network import run
    out=tmp_path_factory.mktemp('causal-invariance')
    @lru_cache(None)
    def result(tolerance,variant):
        s=case('two_active_limiters_stress')
        if variant=='noop':
            s.coupled.events += [CoupledEvent(time_s=.1,kind='voltage_reference_step',equipment_id=g,value=0.) for g in ('g','g2')]
        elif variant=='permutation':
            s.coupled.underexcitation_limiters.reverse();s.coupled.esst1a_exciters.reverse()
        s=StudySpec.model_validate(s.model_dump(mode='json'))
        b=run(s,out/(str(tolerance)+'-'+variant),Path(root),tolerance,native_uels=True)
        assert b['completed'] and b['native_UEL_event_policy']=='causal'
        return s,b
    return result


@pytest.mark.parametrize('tolerance',[1e-11,1e-12])
@pytest.mark.parametrize('variant',['noop','permutation'])
def test_causal_reference_is_invariant_to_noop_commands_and_passport_order(native_pair,tolerance,variant):
    from benchmarks.andes_joint_double_cage import sample_native
    from power_grid_env.model import canonical_hash
    s,a=native_pair(tolerance,'baseline');modified,b=native_pair(tolerance,variant)
    assert canonical_hash(s)!=canonical_hash(modified)
    clocks=reset_distance(a,b,True,True);assert clocks['passed'],clocks
    # Native roots can produce several zero-time observations. Use the same
    # established first-pre/last-post observation contract as the main audit.
    a=coalesce_zero_time_batches(a);b=coalesce_zero_time_batches(b)
    times=np.arange(round(s.coupled.duration_s/s.coupled.step_s)+1)*s.coupled.step_s
    def observed_sides(r):
        return ['post_event' if any(abs(t-at)<1e-12 and side=='post_event' for t,side in zip(r['time_s'],r['sample_side'])) else 'regular' for at in times]
    left_sides=observed_sides(a);right_sides=observed_sides(b)
    assert a['channels'].keys()==b['channels'].keys()
    for key in a['channels']:
        error=max(abs(sample_native(a,times,left_sides,key)-sample_native(b,times,right_sides,key)))
        limit=100. if key.endswith(('.p_w','.q_var')) else 0. if key.endswith(('.connected','.active','_enabled','_latched')) else 2e-5
        assert math.isfinite(error) and error<=limit,(key,error,limit)
    levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in s.network.voltage_levels}
    for node in s.network.connectivity_nodes:
        for component in ('real_v','imag_v'):
            traces=[dict(time_s=r['time_s'],sample_side=r['sample_side'],channels={'v':r['node_voltage'][node.id][component]}) for r in (a,b)]
            error=max(abs(sample_native(traces[0],times,left_sides,'v')-sample_native(traces[1],times,right_sides,'v')))/levels[node.voltage_level_id]
            assert error<=2e-5
    assert b['native_UEL_event_iterations']['skipped_unaffected']>0
    for e in b['event_log']:
        if e['kind']=='native_mnlex2_lag_reset':assert e['event_iteration_reasons'] and 'legacy_broadcast' not in e['event_iteration_reasons']


@pytest.mark.parametrize('tolerance',[1e-11,1e-12])
def test_causal_two_active_limiters_agree_with_production(native_pair,tolerance):
    s,b=native_pair(tolerance,'baseline');a=run_study(s);assert a['completed']
    c=compare(s,a,b);assert c['passed'],c
    assert not c['reset_failures']
    for gid in ['g','g2']:assert max(a['channels'][gid+'.UEL.output_pu'])>.01


@pytest.fixture
def fake_causal(monkeypatch):
    from benchmarks import generator_esst1a_causal_validation as m,mnlex2_model_exchange
    monkeypatch.setattr(mnlex2_model_exchange,'verify',lambda root:{})
    monkeypatch.setattr(m,'run_study',lambda s:dict(completed=True))
    def run(s,out,*args,**kwargs):Path(out).mkdir();return dict(completed=True,native_UEL_event_policy='causal',native_UEL_root_policy='continuous_atoms',native_MNLEX2_in_feedback=True,event_log=[])
    monkeypatch.setattr(m,'run',run)
    return m


def test_causal_qualifier_never_accepts_a_negative_witness_as_agreement(fake_causal,tmp_path,monkeypatch):
    m=fake_causal;monkeypatch.setattr(m,'compare',lambda *args:dict(passed=False,reason='known_timing_error'))
    r=m.qualify(tmp_path/'out',tmp_path/'runtime',names=('two_limiters_sensitive',))
    assert not r['passed'] and not r['all_registered_trajectories_agree'] and not r['expected_rejections_accepted']


def test_causal_qualifier_refuses_broadcast_evidence(fake_causal,tmp_path,monkeypatch):
    m=fake_causal
    def run(s,out,*args,**kwargs):Path(out).mkdir();return dict(native_UEL_event_policy='broadcast')
    monkeypatch.setattr(m,'run',run)
    with pytest.raises(ValueError,match='causal native'):m.qualify(tmp_path/'out',tmp_path/'runtime',names=('hold',))
    assert not json.loads((tmp_path/'out/summary.json').read_text())['passed']



def test_causal_qualifier_refuses_compound_root_profile(fake_causal,tmp_path,monkeypatch):
    m=fake_causal
    def run(s,out,*args,**kwargs):
        Path(out).mkdir();return dict(completed=True,native_UEL_event_policy='causal',native_UEL_root_policy='compound_and_atoms',native_MNLEX2_in_feedback=True,event_log=[])
    monkeypatch.setattr(m,'run',run)
    with pytest.raises(ValueError,match='continuous native'):m.qualify(tmp_path/'out',tmp_path/'runtime',names=('hold',))
    assert not json.loads((tmp_path/'out/summary.json').read_text())['passed']

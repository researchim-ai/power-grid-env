"""Refinement must distinguish self-convergence from independent agreement."""
import json,math
from pathlib import Path
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from benchmarks import generator_esst1a_refinement as module
from power_grid_env.generator_underexcitation import MinimumExcitationBank,UnderexcitationLimiter,mnlex2_rates


def trace(times=(.2,),native=False,bound='lower'):
    return dict(completed=True,event_log=[dict(kind='native_mnlex2_lag_reset' if native else 'uel_lag_reset',time_s=t,equipment_id='g',raw_state_before_pu=-.2,raw_state_after_pu=0.,bound=bound) for t in times])


@pytest.mark.parametrize('change',['time','inventory','bound','jump','nonfinite','unordered'])
def test_reset_refinement_detects_all_mismatches(change):
    a=trace((.2,.4));b=trace((.2,.4),True)
    if change=='time':b['event_log'][-1]['time_s']+=2e-6
    elif change=='inventory':b['event_log'].pop()
    elif change=='bound':b['event_log'][-1]['bound']='upper'
    elif change=='jump':b['event_log'][-1]['raw_state_before_pu']+=.001
    elif change=='nonfinite':b['event_log'][-1]['time_s']=float('nan')
    elif change=='unordered':b['event_log'].reverse()
    try:r=module.reset_distance(a,b,False,True)
    except ValueError:
        assert change in ('nonfinite','unordered');return
    assert not r['passed']
    if change=='time':assert r['pairs'][0]['time_error_s']==0 and r['pairs'][1]['time_error_s']>1e-6


def test_no_reset_and_identical_reset_are_legitimate_self_convergence():
    for times in [(),(.2,.4)]:
        r=module.reset_distance(trace(times),trace(times,True),False,True)
        assert r['passed'] and r['maximum_time_error_s']==0


@pytest.mark.parametrize('primary,native',[
    (module.PRIMARY[:1],module.NATIVE),(module.PRIMARY,module.NATIVE[:1]),
    (module.PRIMARY[::-1],module.NATIVE),(module.PRIMARY,module.NATIVE[::-1]),
    ((module.PRIMARY[0],module.PRIMARY[0]),module.NATIVE),
    (((1e-11,1e-13,1.),(1e-12,1e-14,2.)),module.NATIVE),
    (((1e-11,1e-13,1.),(1e-14,1e-15,.5)),module.NATIVE),
    (((float('nan'),1e-13,1.),module.PRIMARY[1]),module.NATIVE),
    (module.PRIMARY,(1e-11,float('inf'))),
])
def test_invalid_refinement_tiers_fail_before_any_native_compilation(primary,native):
    with pytest.raises(ValueError):module.validate_tiers(primary,native)


@pytest.mark.parametrize('slope',[1e-8,1e-7,1e-6])
@pytest.mark.parametrize('side',['lower','upper'])
def test_actual_guard_delay_matches_independent_closed_form_ramp(slope,side):
    # Below the lower bound the feedback is zero. For error=e0+s*t:
    # raw'=K*s*(1-exp(-t/T)); the biased guard fires at eps, not at 0.
    p=UnderexcitationLimiter(generator_id='g',K_M=1.,T_M=1.,MEL_MAX=1e-12)
    raw0=-1e-14 if side=='lower' else p.MEL_MAX+1e-14
    memory0=0. if side=='lower' else p.MEL_MAX;direction=1. if side=='lower' else -1.
    eps=64*np.finfo(float).eps
    expected=-p.T_M*math.log1p(-eps/(p.K_M*slope))
    bank=MinimumExcitationBank.__new__(MinimumExcitationBank)
    bank.entries={0:(0,p)};bank.regions={0:-1 if side=='lower' else 1}
    def rate(x,i):return mnlex2_rates(p,x[:2],raw0/p.K_M+direction*slope*x[2])[0]
    bank.free_rate=rate
    _,guards=bank.guards(np.array([raw0,memory0,0.]))
    def rhs(t,x):return [*mnlex2_rates(p,x[:2],raw0/p.K_M+direction*slope*x[2]),1.]
    a=solve_ivp(rhs,(0.,expected*4),[raw0,memory0,0.],method='Radau',rtol=1e-11,atol=[1e-28,1e-28,1e-16],events=guards,max_step=expected/3.7)
    assert a.success and len(a.t_events[0])==1
    assert a.t_events[0][0]==pytest.approx(expected,rel=1e-6,abs=1e-14)
    if slope==1e-8:assert a.t_events[0][0]>module.EVENT_LIMIT


@pytest.fixture
def fake_native(monkeypatch):
    from benchmarks import mnlex2_model_exchange
    monkeypatch.setattr(mnlex2_model_exchange,'verify',lambda root:dict(pinned=True))
    seen=[]
    def primary(spec):
        seen.append((spec.coupled.rtol,spec.coupled.atol,spec.coupled.step_s))
        return trace((.200002,))
    def native(spec,out,root,tolerance,native_uels):
        assert native_uels;Path(out).mkdir();return trace((.2,),True)
    def compare(spec,a,b):
        # Self-converged trajectories can still disagree across solvers.
        return dict(passed=False,reset_failures=[dict(reason='timing')],native_UEL_reset_checks=[dict(time_error_s=2e-6)])
    monkeypatch.setattr(module,'audit',lambda *args:dict(passed=True))
    monkeypatch.setattr(module,'run_study',primary);monkeypatch.setattr(module,'native_run',native);monkeypatch.setattr(module,'compare',compare)
    return seen


def test_complete_refinement_cannot_turn_self_convergence_into_validation(tmp_path,fake_native):
    r=module.refine(tmp_path/'out',tmp_path/'runtime',names=('two_limiters_sensitive',))
    assert r['passed'] and r['diagnostic_completed']
    assert not r['all_cross_solver_comparisons_agree'] and not r['industrial_profile_qualified']
    row=r['runs'][0]
    assert row['primary_reset_clocks_self_converged'] and row['native_reset_clocks_self_converged']
    assert len(row['cross_solver'])==6 and not any(c['passed'] for c in row['cross_solver'])
    assert [v[2] for v in fake_native]==[.005,.0025,.00125]
    protocol=json.loads((tmp_path/'out/protocol.json').read_text())
    assert protocol['negative_witnesses_accepted_as_agreement'] is False


def test_failed_primary_does_not_publish_success_or_start_native(tmp_path,fake_native,monkeypatch):
    monkeypatch.setattr(module,'run_study',lambda s:dict(completed=False,status='numerically_not_converged'))
    def forbidden(*args,**kwargs):raise AssertionError('native called after primary failure')
    monkeypatch.setattr(module,'native_run',forbidden)
    with pytest.raises(ValueError,match='primary refinement failed'):module.refine(tmp_path/'out',tmp_path/'runtime',names=('hold',))
    assert json.loads((tmp_path/'out/summary.json').read_text())['passed'] is False


@pytest.mark.parametrize('names',[(),('hold','hold'),('unknown',)])
def test_bad_case_inventory_creates_no_output(tmp_path,names):
    with pytest.raises(ValueError):module.refine(tmp_path/'out',tmp_path/'runtime',names=names)
    assert not (tmp_path/'out').exists()


def test_case_dispatch_preserves_old_passports_and_keeps_stress_separate():
    from benchmarks.generator_esst1a_joint_cases import NAMES,example
    for name in NAMES:assert module.case(name).model_dump()==example(name).model_dump()
    stress=module.case(module.STRESS)
    assert stress.id.endswith('stress') and module.STRESS not in NAMES
    assert len(stress.coupled.underexcitation_limiters)==2
    assert all(p.Radius==.15 for p in stress.coupled.underexcitation_limiters)


def test_native_failure_leaves_incomplete_diagnostic(tmp_path,fake_native,monkeypatch):
    def failed(spec,out,*args,**kwargs):
        Path(out).mkdir();return dict(completed=False)
    monkeypatch.setattr(module,'native_run',failed)
    with pytest.raises(ValueError,match='native refinement failed'):module.refine(tmp_path/'out',tmp_path/'runtime',names=('hold',))
    assert json.loads((tmp_path/'out/summary.json').read_text())['status']=='running'


def test_producer_change_during_refinement_prevents_success(tmp_path,fake_native,monkeypatch):
    from benchmarks import native_online_validation
    calls=[]
    def identity():calls.append(1);return len(calls)
    monkeypatch.setattr(native_online_validation,'source_identity',identity)
    with pytest.raises(ValueError,match='producer changed'):module.refine(tmp_path/'out',tmp_path/'runtime',names=('hold',))
    assert json.loads((tmp_path/'out/summary.json').read_text())['passed'] is False


def test_existing_refinement_output_is_never_overwritten(tmp_path,fake_native):
    out=tmp_path/'out';out.mkdir();p=out/'summary.json';p.write_text('keep')
    with pytest.raises(FileExistsError):module.refine(out,tmp_path/'runtime',names=('hold',))
    assert p.read_text()=='keep'

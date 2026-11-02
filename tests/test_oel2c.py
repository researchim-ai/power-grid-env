"""Analytic block limits, source semantics and complete coupled native feedback."""
from functools import lru_cache
from pathlib import Path
import copy,os
import numpy as np
import pytest
from pydantic import ValidationError
from scipy.linalg import expm
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics
from power_grid_env.study_spec import run_study,StudySpec
from benchmarks.generator_oel2c_cases import example,NAMES
from benchmarks.generator_oel2c_validation import negative,NEGATIVE,compare
from benchmarks.oel2c_native_reference import primary,example as component


@pytest.fixture(scope='module')
def calculated():
    @lru_cache(None)
    def evaluate(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=120.)
        assert r['completed'],r
        return s,r
    return evaluate


@pytest.fixture(scope='module')
def native_calculated(tmp_path_factory):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned Dynawo/Modelica/FMI reference not configured')
    from benchmarks.oel2c_model_exchange import run
    out=tmp_path_factory.mktemp('oel2c-fmi')
    @lru_cache(None)
    def evaluate(name):return run(example(name),out/name,Path(root),1e-10)
    return evaluate


@pytest.mark.parametrize('name',NAMES)
def test_full_cold_plant_is_finite_and_bounded(name,calculated):
    s,r=calculated(name);assert r['additional_validation']['oel2c_limits']['g2']['within_limits']
    p=s.coupled.oel2c_limiters[0];ch=r['channels']
    assert min(ch['g2.OEL2C.output_pu'])>=p.VOel1MinPu and max(ch['g2.OEL2C.output_pu'])<=0
    assert min(ch['g2.OEL2C.reference_current_pu'])>=p.ILimPu-1e-9
    assert max(ch['g2.OEL2C.reference_current_pu'])<=p.IInstPu+1e-9
    assert r['operating_point']['oel2c_initialization'].startswith('dynawo_startup')


@pytest.mark.parametrize('name',NAMES)
def test_native_modelica_limiter_closes_feedback_on_independent_plant(name,calculated,native_calculated):
    s,r=calculated(name);native=native_calculated(name)
    assert native['native_OEL2C_in_feedback'] and not native['production_limiter_RHS_used'] and not native['primary_event_clocks_used']
    assert compare(s,r,native)['passed']


@pytest.mark.parametrize('name',NEGATIVE)
def test_unsupported_profiles_do_not_return_partial_success(name):
    data=negative(name)
    try:s=StudySpec.model_validate(data)
    except ValidationError:return
    r=run_study(s,isolate=True,wall_timeout_s=120.)
    assert not r['completed'] and 'channels' not in r


@pytest.mark.parametrize('field,bad',[('tROel',0),('tAOel',0),('tDOel',0),('tC1Oel',0),('KiOel',0),('KFb',0),('KScale',0),('KAct',0),('C1',0),('Krd',.1),('FixedRu',.1),('VOel1MaxPu',.01),('IInstPu',.1),('tMax',0),('cap_tolerance_pu',0),('cap_tolerance_pu',1e-4),('tEn',-1),('Kzru',0),('C2',float('nan'))])
def test_degenerate_constants_cannot_silently_choose_a_different_model(field,bad):
    with pytest.raises(ValidationError):OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,**{field:bad})


def test_constant_input_has_analytic_ramp_filter_and_antiwindup_solution():
    case=component('hold');r=primary(case);p=OEL2CPassport.model_validate(case['passport']);a=p.KpOel+p.KdOel/p.tDOel;b=p.KiOel*(1-a/.9)
    # Independent augmented linear system: reference, filtered reference,
    # integral output, derivative memory, affine constant. PID upper-held.
    A=np.zeros((5,5));A[0,4]=p.Kru;A[1,:2]=[1/p.tAOel,-1/p.tAOel]
    A[2,1:4]=[b,-p.KiOel/.9,p.KiOel/.9];A[2,4]=-b*.8
    A[3,1]=p.KdOel/p.tDOel**2;A[3,3]=-1/p.tDOel;A[3,4]=-.8*p.KdOel/p.tDOel**2
    x0=np.array([.8,.8,0.,0.,1.])
    keys=['reference_current_pu','filtered_reference_pu','pid_integral_pu','pid_derivative_memory_pu']
    for j,t in enumerate(r['time_s']):assert [r['channels'][k][j] for k in keys]==pytest.approx((expm(A*t)@x0)[:4],abs=2e-8)
    assert max(abs(np.array(r['channels']['timer_s'])+.46))<1e-10
    assert max(abs(np.array(r['channels']['output_pu'])))<1e-12


def test_constant_overload_reaches_lower_reference_at_exact_time():
    case=component('constant_overload');r=primary(case);p=OEL2CPassport.model_validate(case['passport'])
    expected=np.maximum(p.ILimPu,1.2+p.Krd*np.asarray(r['time_s']))
    assert r['channels']['reference_current_pu']==pytest.approx(expected,abs=1e-9)
    hits=[e for e in r['event_log'] if e['transition']=='reference_lower']
    assert len(hits)==1 and hits[0]['time_s']==pytest.approx(1.,abs=1e-9)


def test_activation_and_reset_clocks_cancel_and_obey_priority():
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.);k=OEL2CDynamics(p);x=k.initial(.8);x[0]=1.3;x[2]=1.2;log=[]
    k.apply(set(),2.,x,log);assert k.context['enable_start']==2. and not k.context['enable_expired']
    k.apply({'enable_deadline'},2.1,x,log);assert k.context['enable_expired']
    x[0]=.8;x[2]=p.IInstPu;k.apply(set(),3.,x,log)
    assert k.context['enable_start'] is None and k.context['reset_start']==3.
    k.apply({'reset_deadline'},3.1,x,log);assert k.quantities(x)['bias']==p.IResetPu
    x[1]=p.tFcl+.01;k.apply(set(),3.2,x,log);assert k.quantities(x)['bias']==0
    p.tEn=0.;x[1]=-.46;k.apply(set(),3.3,x,log);assert k.quantities(x)['bias']==0


def test_upper_cap_guard_is_explicit_and_not_exact_float_equality():
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.);k=OEL2CDynamics(p);x=k.initial(.8);x[2]=p.IInstPu-p.cap_tolerance_pu/2
    k.apply(set(),1.,x,[]);k.context['reset_expired']=True
    assert x[2]!=p.IInstPu and k.quantities(x)['bias']==p.IResetPu
    x[2]=p.IInstPu-2*p.cap_tolerance_pu;k.apply(set(),1.1,x,[]);k.context['reset_expired']=True
    assert k.quantities(x)['bias']==0


def test_feedback_reduces_actual_field_current_without_hard_clipping(calculated):
    s,r=calculated('voltage_step');u=s.model_copy(deep=True);u.coupled.oel2c_limiters=[];base=run_study(u);assert base['completed']
    from benchmarks.generator_excitation_validation import reaction_from_trace
    p=s.coupled.oel2c_limiters[0];current=reaction_from_trace(u,base,'g2')/p.rated_field_reaction_pu
    assert min(r['channels']['g2.OEL2C.output_pu'])<-.01
    assert r['channels']['g2.OEL2C.field_current_input_pu'][-1]<current[-1]-.05
    assert max(abs(np.array(r['channels']['g2.OEL2C.field_reaction_pu'])-r['channels']['g2.SEXS.field_pu']))>.05


def test_OEL2C_and_UEL_have_documented_opposite_summing_signs(calculated):
    s,r=calculated('UEL_coordination');ch=r['channels']
    expected=np.asarray(ch['g2.SEXS.reference_pu'])-ch['g2.terminal_voltage_pu']+np.asarray(ch['g2.UEL.output_pu'])+ch['g2.OEL2C.output_pu']
    assert ch['g2.SEXS.error_pu']==pytest.approx(expected,abs=1e-12)
    assert max(ch['g2.UEL.output_pu'])>.001 and min(ch['g2.OEL2C.output_pu'])<-.001


def test_post_field_quench_keeps_analytic_rotor_and_heat_memory(calculated):
    from benchmarks.generator_field_validation import analytic_quench
    s,r=calculated('field_trip');assert analytic_quench(s,r)['passed']
    assert r['channels']['g2.field_voltage_enabled'][-1]==0
    assert r['channels']['g2.OEL2C.measured_current_pu'][-1]>0


@pytest.mark.parametrize('factor',[.1,7.])
def test_machine_field_base_is_independent_of_network_power_base(factor,calculated):
    s,r=calculated('voltage_step');s=s.model_copy(deep=True);s.network.s_base_va*=factor;v=run_study(s);assert v['completed']
    assert v['channels']['g2.OEL2C.output_pu']==pytest.approx(r['channels']['g2.OEL2C.output_pu'],abs=2e-7)


def test_physics_and_targeting_survive_passport_permutations(calculated):
    s,r=calculated('two_limiters');s=s.model_copy(deep=True);s.coupled.machines.reverse();s.coupled.oel2c_limiters.reverse();v=run_study(s);assert v['completed']
    for gid in ['g','g2']:assert v['channels'][gid+'.OEL2C.output_pu']==pytest.approx(r['channels'][gid+'.OEL2C.output_pu'],abs=2e-7)


def test_legacy_oracles_cannot_silently_omit_new_limiter():
    from benchmarks.multibus_operating_point import independent_circuit
    from benchmarks import andes_group_motor_reference,andes_joint_motor_reference,andes_multibus_motor
    s=example('hold')
    with pytest.raises(ValueError,match='OEL2C'):independent_circuit(s)
    for module,args in [(andes_group_motor_reference,(s,)),(andes_joint_motor_reference,(s,)),(andes_multibus_motor,(s,.0001))]:
        with pytest.raises(ValueError,match='OEL2C'):module.scope(*args)
    from power_grid_env.coupled_small_signal import run_coupled_small_signal
    assert run_coupled_small_signal(s.network,s.coupled)['reason']=='oel2c_modal_analysis_unsupported'


def test_missing_native_limiter_memory_is_a_failure(calculated,native_calculated):
    s,r=calculated('hold');ref=copy.deepcopy(native_calculated('hold'))
    del ref['channels']['g2.OEL2C.leadlag1_memory_pu']
    with pytest.raises(ValueError,match='missing native OEL2C channels'):compare(s,r,ref)


def test_four_native_blocks_keep_distinct_targets(calculated):
    s,r=calculated('four_machines')
    assert len(s.coupled.oel2c_limiters)==4
    currents=[np.asarray(r['channels'][g+'.OEL2C.field_current_input_pu']) for g in ['g','g2','g3','g4']]
    assert all(np.isfinite(a).all() for a in currents)
    assert max(abs(currents[0]-currents[1]))>.01


@pytest.mark.parametrize('label,branch',[('enable','above'),('reset','below')])
def test_same_instant_cancellation_has_priority_over_expiring_deadline(label,branch):
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.);k=OEL2CDynamics(p);x=k.initial(.8)
    if label=='enable':x[0]=1.3;x[2]=1.2
    else:x[0]=.8;x[2]=p.IInstPu
    k.apply(set(),1.,x,[]);assert k.context[label+'_start']==1.
    if label=='enable':x[0]=1.1
    else:x[2]=.8
    log=[];k.apply({'branch_'+branch,label+'_deadline'},1.1,x,log)
    assert k.context[label+'_start'] is None and not k.context[label+'_expired']
    assert not any(e['transition']==label+'_deadline' for e in log)
    # Reprocessing a stale root is also harmless.
    k.apply({label+'_deadline'},1.1,x,[])
    assert not k.context[label+'_expired']

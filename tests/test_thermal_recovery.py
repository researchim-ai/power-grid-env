import copy
import numpy as np
import pytest
from types import SimpleNamespace
from power_grid_env.generator_thermal import GeneratorThermalProtection,GeneratorThermalBank
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.thermal_recovery_cases import SCENARIOS,example
from benchmarks.thermal_recovery_reference import ThermalRecoveryReference
from benchmarks.thermal_recovery_benchmark import compare,coverage,physical_gate
from benchmarks.standby_interlocks_benchmark import report_sample


@pytest.fixture(scope='module')
def refs():return {s:ThermalRecoveryReference(example(s)).run() for s in SCENARIOS}


@pytest.fixture(scope='module')
def reports():
    rows={}
    for s in SCENARIOS:
        for engine in ('radau','network_dae'):
            data=example(s);data['coupled']['integration_method']=engine
            rows[s,engine]=run_study(StudySpec.model_validate(data))
    return rows


@pytest.mark.parametrize('scenario',SCENARIOS)
@pytest.mark.parametrize('engine',['radau','network_dae'])
def test_reset_and_retrip_match_independent_equations(scenario,engine,refs,reports):
    r=reports[scenario,engine];assert r['completed'] and r['additional_validation']['ok']
    assert compare(refs[scenario],r)['passed']
    assert coverage(refs[scenario].events,scenario)==coverage(r['event_log'],scenario)
    physical_gate(report_sample(r),r['event_log'],scenario)


def bank(reset=.1):
    p=GeneratorThermalProtection(generator_id='g',time_constant_s=2.,trip_level=1.,reset_level=reset)
    m=SimpleNamespace(generators=[SimpleNamespace(id='g')],settings=SimpleNamespace(generator_thermal=[p]),gen_connected=np.array([False]))
    b=GeneratorThermalBank(m,0);b.latched[0]=True
    return m,b


@pytest.mark.parametrize('level,outcome',[(.1000000001,'blocked'),(.1,'applied'),(.099,'applied')])
def test_reset_threshold_is_conservative_and_does_not_erase_memory(level,outcome):
    m,b=bank();x=np.array([level]);old=x.copy();r=b.reset('g',x)
    assert r['outcome']==outcome and np.array_equal(x,old)
    assert not m.gen_connected[0] and b.latched[0]==(outcome=='blocked')
    assert b.derivatives(x,np.zeros(1))[0]==pytest.approx(-level/2)


def test_reset_requires_open_generator_and_explicit_passport():
    m,b=bank();x=np.array([.01]);m.gen_connected[0]=True
    assert b.reset('g',x)['reason']=='generator_connected' and b.latched[0]
    m,b=bank(None)
    assert b.reset('g',x)['reason']=='thermal_reset_disabled' and b.latched[0]
    with pytest.raises(ValueError,match='unknown'):b.reset('missing',x)


@pytest.mark.parametrize('value',[-.1,1.,2.,float('nan'),float('inf')])
def test_invalid_reset_hysteresis_rejected(value):
    with pytest.raises(ValueError):GeneratorThermalProtection(generator_id='g',time_constant_s=2.,trip_level=1.,reset_level=value)


@pytest.mark.parametrize('fault',['disabled','unknown','no_actuator','no_automation','value'])
def test_invalid_reset_command_cannot_run(fault):
    s=example('manual_retrip');c=s['coupled'];event=next(e for e in c['events'] if e['kind']=='generator_thermal_reset')
    if fault=='disabled':c['generator_thermal'][0].pop('reset_level')
    elif fault=='unknown':event['equipment_id']='missing'
    elif fault=='no_actuator':
        c['network_automation']['transfers']=[];c['network_automation']['breakers']=[b for b in c['network_automation']['breakers'] if b['target_kind']!='generator']
        c['events']=[event]
    elif fault=='no_automation':c['network_automation']=None;c['events']=[event]
    else:
        event['value']=0.
        with pytest.raises(ValueError):StudySpec.model_validate(s)
        return
    assert not run_study(StudySpec.model_validate(s))['completed']


def test_old_passport_serialization_unchanged():
    p=GeneratorThermalProtection(generator_id='g',time_constant_s=1.,trip_level=1.)
    assert 'reset_level' not in p.model_dump(mode='json')


def test_thermal_opening_time_survives_acknowledgement(refs):
    r=refs['minimum_open'];trip=next(e for e in r.events if e['kind']=='generator_thermal_trip')
    assert .115-trip['time_s']<.08
    blocked=next(e for e in r.events if e.get('synchronism',{}).get('reason')=='minimum_open_time')
    assert blocked['time_s']==pytest.approx(.115)
    assert r.sample(.106)['g.stator_thermal_level']>0


def test_DAE_token_invalidated_by_latch_only_change():
    from tests.test_two_cage_winding import initial
    from power_grid_env.dae import mode_token
    m,x,_=initial(example());old=mode_token(m);m.generator_thermal.latched[0]=True
    assert mode_token(m)!=old


@pytest.mark.parametrize('field',['level','threshold','outcome','latch'])
def test_tampered_reset_evidence_rejected(field,refs,reports):
    r=copy.deepcopy(reports['manual_retrip','radau'])
    if field=='latch':r['channels']['g.stator_thermal_latched'][-1]=0.
    else:
        e=next(e for e in r['event_log'] if e['kind']=='generator_thermal_reset')
        e[field]='blocked' if field=='outcome' else e[field]+.01
    assert not compare(refs['manual_retrip'],r)['passed']


def test_independent_reset_never_calls_native_protection(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('native protection called')
    for name in ('reset','apply','derivatives'):monkeypatch.setattr(GeneratorThermalBank,name,forbidden)
    r=ThermalRecoveryReference(example('ats_retrip')).run()
    assert coverage(r.events,'ats_retrip')['thermal_trips']==2

from pathlib import Path
import copy
import pytest
from power_grid_env.study_artifacts import read_json
from power_grid_env.closed_loop_protection import ClosedLoopPlan
from benchmarks.full_protection_duty import duty
ROOT=Path(__file__).resolve().parents[1]


def fixture():
    p=ClosedLoopPlan.model_validate(read_json(ROOT/'cases/full-protection-envelope/radau.json')['base'])
    r=dict(time_s=[0,.1,.1,.2,.2,.3],sample_side=['initial','pre_event','post_event','pre_event','post_event','regular'],
        event_log=[dict(kind='breaker_open',equipment_id='primary-cb',outcome='applied',time_s=.2)],channels={
            'feeder.from_current_a':[5,5,100,100,0,0],
            'backup-oc.measurement':[5,5,100,100,0,0],
            'backup-oc.measurement_valid':[1]*6})
    return p,r


def test_exact_event_sided_i2t_and_pre_open_current():
    # Numerical duty-only fixture, not a claimed valid relay-trip trajectory.
    p,r=fixture();v=duty(p,'primary',r)
    assert v['passed'] and v['cable_i2t_a2s']==pytest.approx(1000)
    assert v['clearing_s']==pytest.approx(.1) and v['pre_opening_rms_a']==100


def test_linear_ramp_squared_integral():
    p,r=fixture();r['channels']['feeder.from_current_a'][3]=200
    v=duty(p,'primary',r)
    assert v['cable_i2t_a2s']==pytest.approx(.1*(100**2+100*200+200**2)/3)


def test_primary_budget_failure():
    p,r=fixture();p.equipment.sizing.installation.clearing_s=.01
    v=duty(p,'primary',r)
    assert not v['passed'] and not v['checks']['primary_owner_clearing_budget']


def test_backup_uses_own_limits_and_actual_time():
    p,r=fixture();r['event_log'][0]['equipment_id']='backup-cb'
    p.equipment.sizing.installation.clearing_s=.01
    v=duty(p,'breaker_failure',r)
    assert v['passed'] and v['checks']['primary_owner_clearing_budget']
    p.backup.interrupting_a=50
    assert not duty(p,'breaker_failure',r)['checks']['device_interrupting']

@pytest.mark.parametrize('defect',['missing_pre','bad_measurement','negative','time_reverse','no_open','wrong_kind'])
def test_invalid_trace(defect):
    p,r=fixture();kind='primary'
    if defect=='missing_pre':r['sample_side'][3]='regular'
    if defect=='bad_measurement':r['channels']['backup-oc.measurement_valid'][2]=0
    if defect=='negative':r['channels']['feeder.from_current_a'][2]=-1
    if defect=='time_reverse':r['time_s'][3]=.09
    if defect=='no_open':r['event_log']=[]
    if defect=='wrong_kind':kind='normal'
    with pytest.raises(ValueError):duty(p,kind,r)

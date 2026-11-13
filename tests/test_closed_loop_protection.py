import copy
from pathlib import Path
import pytest
from power_grid_env.closed_loop_protection import compile_closed_loop,run_closed_loop,verify_closed_loop,action_check
from power_grid_env.study_artifacts import read_json,encoded,seal
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def plan():return read_json(ROOT/'cases/closed-loop-protection/radau.json')

def small(plan):
    p=copy.deepcopy(plan);p['equipment']['mandatory_cases']=p['equipment']['mandatory_cases'][:1]
    c=p['equipment']['mandatory_cases'][0];c['study']['induction'].update(duration_s=.2,step_s=.001,events=[])
    c['motors'][0]['minimum_final_speed_pu']=0
    c['voltage_windows']=[dict(node_id='n1',from_s=0,to_s=.2,minimum_voltage_pu=0,maximum_voltage_pu=2)]
    p.update(fault_duration_s=.6,fault_clear_s=.5)
    p['fault_voltage_windows'][0]['to_s']=.6
    return p

def test_compile_preserves_normal_brief_and_selected_physics(plan):
    old=copy.deepcopy(plan);p,pack,kinds,static=compile_closed_loop(plan)
    assert plan==old and len(pack.cases)==6
    assert static['passes_declared_checks']
    for original,protected in zip(p.equipment.mandatory_cases,pack.cases):
        assert protected.motors==original.motors and protected.voltage_windows==original.voltage_windows
        assert protected.study.coupled.machines==[]
        assert protected.study.network.generators==[]
        assert protected.study.network.sources[0].r_ohm==pytest.approx(.01024)
        assert protected.study.network.lines[0].r_ohm[0][0]==pytest.approx(.0258399015)
    assert set(kinds.values())=={'normal','primary','breaker_failure','measurement_loss'}

@pytest.mark.parametrize('change', ['inverse','early_clear','horizon','node','criteria','collision'])
def test_invalid_plan(plan,change,tmp_path):
    if change=='inverse':plan['primary']['curve']='standard_inverse'
    if change=='early_clear':plan['fault_clear_s']=.2
    if change=='horizon':plan['fault_clear_s']=plan['fault_duration_s']
    if change=='node':plan['fault_node_id']='n0'
    if change=='criteria':plan['fault_motors'][0]['motor_id']='missing'
    if change=='collision':
        # A reserved scenario ID cannot replace a normal scenario.
        plan['equipment']['mandatory_cases'][0]['id']='fault-primary'
    with pytest.raises(ValueError):run_closed_loop(plan,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()

@pytest.fixture(scope='module',params=['radau','network_dae'])
def packet(request,tmp_path_factory):
    p=small(read_json(ROOT/'cases/closed-loop-protection/radau.json'));p['integration_method']=request.param
    out=tmp_path_factory.mktemp(request.param)/'study';r=run_closed_loop(p,out)
    return out,p,r

def test_actual_actions_and_physical_audits(packet):
    out,p,r=packet
    assert r['completed'] and r['numerically_qualified'] and r['passes_declared_profile']
    assert r['reward'] is None and not r['normative_approval']
    assert verify_closed_loop(out,expected_plan=p)['verified']
    assert len(list((out/'studies').glob('*/grid-*/result.json')))==12
    failed=read_json(out/'studies/fault-breaker_failure/grid-0/result.json')
    assert any(e['kind']=='breaker_failure_confirmed' for e in failed['event_log'])
    assert failed['channels']['backup-cb.closed'][-1]==0
    assert failed['channels']['primary-cb.closed'][-1]==1


def test_independent_si_algebra(packet):
    from benchmarks.closed_loop_protection_reference import algebra
    out,p,_=packet;_,pack,_,_=compile_closed_loop(p)
    for case in pack.cases:
        report=read_json(out/'studies'/case.id/'grid-0/result.json')
        assert algebra(case,report)['passed']


def test_owner_anchor(packet):
    out,p,_=packet;p=copy.deepcopy(p);p['primary']['pickup_a']=1100
    with pytest.raises(ValueError,match='owner closed-loop'):verify_closed_loop(out,expected_plan=p)

@pytest.mark.parametrize('defect',['reward','action','compiled'])
def test_resealed_tampering(packet,tmp_path,defect):
    import shutil
    out,_,_=packet;target=tmp_path/'copy';shutil.copytree(out,target)
    if defect=='compiled':
        path=target/'studies/input.json';data=read_json(path);data['cases'][0]['study']['coupled']['network_automation']['relays'][0]['pickup']=2000
        path.write_bytes(encoded(data));(target/'studies/manifest.json').unlink();seal(target/'studies')
    else:
        path=target/'result.json';data=read_json(path)
        if defect=='reward':data['reward']=1
        else:data['actions'][0]['grids'][0]['checks']['exact_open_actions']=False
        path.write_bytes(encoded(data))
    (target/'manifest.json').unlink();seal(target)
    with pytest.raises(ValueError):verify_closed_loop(target)


def test_plausible_log_without_actuator_state_fails(packet):
    out,p,_=packet
    from power_grid_env.closed_loop_protection import ClosedLoopPlan
    r=read_json(out/'studies/fault-primary/grid-0/result.json')
    r['channels']['primary-cb.closed']=[1.]*len(r['time_s'])
    assert not action_check(ClosedLoopPlan.model_validate(p),'primary',r)['passed']


def test_plausible_log_without_electrical_isolation_fails(packet):
    out,p,_=packet
    from power_grid_env.closed_loop_protection import ClosedLoopPlan
    r=read_json(out/'studies/fault-primary/grid-0/result.json');r['channels']['feeder.from_current_a']=[1.]*len(r['time_s'])
    assert not action_check(ClosedLoopPlan.model_validate(p),'primary',r)['passed']


def test_cli_verifier(packet,capsys):
    from power_grid_env.cli import main
    out,_,_=packet
    assert main(['verify-closed-loop-protection',str(out)])==0
    assert '"verified": true' in capsys.readouterr().out


def test_cancelled_never_passes(plan,tmp_path):
    r=run_closed_loop(small(plan),tmp_path/'cancelled',cancelled=lambda:True)
    assert not r['completed'] and not r['passes_declared_profile'] and r['reward'] is None


def test_motor_only_rejects_loaded_initialization(plan):
    from power_grid_env.coupled_rms import CoupledSettings
    _,pack,_,_=compile_closed_loop(plan);data=pack.cases[0].study.coupled.model_dump(mode='json')
    data['operating_point']={}
    with pytest.raises(ValueError,match='cold initialization'):CoupledSettings.model_validate(data)


def test_missing_generator_passport_still_rejected():
    from benchmarks.network_automation_reference import example
    from power_grid_env.coupled_rms import validate_coupled
    s=example('overcurrent');s.coupled.machines=[]
    assert not validate_coupled(s.network,s.coupled)['ok']

def test_measurement_loss_is_a_scheduled_action(plan):
    _,pack,_,_=compile_closed_loop(plan)
    case=next(c for c in pack.cases if c.id=='fault-measurement_loss')
    relay=next(r for r in case.study.coupled.network_automation.relays if r.id=='primary-oc')
    assert relay.measurement_available
    assert any(e.kind=='relay_measurement_lost' and e.time_s==plan['fault_apply_s']/2 for e in case.study.coupled.events)

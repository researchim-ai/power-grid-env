import copy
from pathlib import Path
import pytest
from power_grid_env.equipment_design import EquipmentDesignTask,compile_equipment,run_equipment,verify_equipment,combined_verdict
from power_grid_env.study_artifacts import read_json,encoded,seal

ROOT=Path(__file__).resolve().parents[1]/'cases/equipment-design'
P=dict(cable_id='c95',transformer_id='t250')
@pytest.fixture
def task():return read_json(ROOT/'task.json')

def small(task):
    task=copy.deepcopy(task)
    task['mandatory_cases']=task['mandatory_cases'][:1]
    c=task['mandatory_cases'][0]
    c['study']['induction'].update(duration_s=.1,step_s=.001,events=[])
    c['motors'][0]['minimum_final_speed_pu']=0
    c['voltage_windows']=[dict(node_id='n1',from_s=0,to_s=.1,minimum_voltage_pu=0,maximum_voltage_pu=2)]
    return task

def test_catalog_enters_actual_physics(task):
    before=copy.deepcopy(task)
    _,_,static,feeder=compile_equipment(task,P)
    from power_grid_env.design_study import compile_design
    _,_,plan=compile_design(feeder,dict(option_id='selected'))
    assert task==before
    for case in plan.cases:
        n=case.study.network
        assert n.sources[0].r_ohm==pytest.approx(.0064)
        assert n.sources[0].x_ohm==pytest.approx((.0256**2-.0064**2)**.5)
        assert n.lines[0].r_ohm[0][0]==pytest.approx(.025)
        assert n.lines[0].x_ohm[0][0]==pytest.approx(.008)
        assert n.lines[0].length_m==100
        assert n.lines[0].rating_a==pytest.approx(191.25)
    assert len(plan.cases)==3
    assert [c.motors for c in plan.cases]==[c.motors for c in EquipmentDesignTask.model_validate(task).mandatory_cases]
    assert static['passes_declared_checks']

def test_transformer_selection_changes_dynamic_source(task):
    alt=copy.deepcopy(task['sizing']['transformers'][0]);alt.update(id='t500',rating_kva=500)
    task['sizing']['transformers'].append(alt)
    a=compile_equipment(task,P)[3]
    b=compile_equipment(task,dict(cable_id='c95',transformer_id='t500'))[3]
    assert b.mandatory_cases[0].study.network.sources[0].r_ohm==a.mandatory_cases[0].study.network.sources[0].r_ohm/2

@pytest.mark.parametrize('mutate',[
    lambda t:t['sizing'].update(demand_kva=1),
    lambda t:t['mandatory_cases'][0]['study']['network']['sources'][0].update(ll_v=410),
    lambda t:t['mandatory_cases'][0]['study']['induction']['motors'][0].update(node_id='n0'),
    lambda t:t['mandatory_cases'][0]['study']['induction']['motors'][0].update(rated_ll_v=410),
    lambda t:t['mandatory_cases'][0]['study']['network']['loads'][0].update(terminal_id='source:n0'),
    lambda t:t['mandatory_cases'][0]['study']['network']['lines'][0].update(temperature_c=90),
    lambda t:t['mandatory_cases'][0]['study']['network']['lines'][0].update(b_siemens=[[.01,0,0],[0,.01,0],[0,0,.01]]),
    lambda t:t['mandatory_cases'][1]['study']['network'].update(name='different'),
    lambda t:t.update(line_id='missing'),
    lambda t:t['mandatory_cases'][0]['motors'][0].update(minimum_final_speed_pu=-1),
])
def test_unsupported_or_mismatched(task,mutate):
    mutate(task)
    with pytest.raises(ValueError):compile_equipment(task,P)

@pytest.mark.parametrize('zero',['cable','transformer'])
def test_static_only_limit_rejected_before_dynamic_output(task,tmp_path,zero):
    if zero=='cable':task['sizing']['cables'][0]['x_ohm_km']=0
    else:task['sizing']['transformers'][0]['resistance_percent']=4
    with pytest.raises(ValueError,match='reactance'):run_equipment(task,P,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()

@pytest.mark.parametrize('bad',[dict(cable_id='foreign',transformer_id='t250'),dict(**P,reward=1)])
def test_proposal_cannot_inject_data(task,bad):
    with pytest.raises(ValueError):compile_equipment(task,bad)

@pytest.fixture(scope='module')
def real_packet(tmp_path_factory):
    task=small(read_json(ROOT/'task.json'));root=tmp_path_factory.mktemp('equipment')/'result'
    result=run_equipment(task,P,root)
    return root,task,result

def test_real_three_grid_workflow(real_packet):
    root,task,r=real_packet
    assert r['completed'] and r['numerically_qualified'] and r['meets_declared_requirements']
    assert r['reward']==1 and not r['normative_approval']
    assert verify_equipment(root,expected_task=task,expected_selection=P)['verified']
    assert len(list((root/'dynamic'/'studies').glob('*/grid-*/result.json')))==3

def test_owner_anchors(real_packet):
    root,task,_=real_packet
    bad=copy.deepcopy(task);bad['sizing']['max_drop_percent']=10
    with pytest.raises(ValueError,match='owner task'):verify_equipment(root,expected_task=bad)
    with pytest.raises(ValueError,match='owner selection'):verify_equipment(root,expected_selection=dict(cable_id='c16',transformer_id='t250'))

def test_resealed_combined_reward_corruption(real_packet,tmp_path):
    import shutil
    root,_,_=real_packet;target=tmp_path/'copy';shutil.copytree(root,target)
    r=read_json(target/'result.json');r['reward']=0
    (target/'result.json').write_bytes(encoded(r));(target/'manifest.json').unlink();seal(target)
    with pytest.raises(ValueError,match='combined verdict'):verify_equipment(target)

def test_resealed_dynamic_source_substitution(real_packet,tmp_path):
    import shutil
    root,_,_=real_packet;target=tmp_path/'copy';shutil.copytree(root,target)
    path=target/'dynamic'/'task.json';task=read_json(path)
    for c in task['mandatory_cases']:c['study']['network']['sources'][0]['r_ohm']*=2
    path.write_bytes(encoded(task))
    for p in [target/'dynamic',target]:(p/'manifest.json').unlink();seal(p)
    with pytest.raises(ValueError,match='owner task'):verify_equipment(target)

def test_real_static_failure_does_not_skip_dynamic(task,tmp_path):
    t=small(task);t['sizing']['installation']['breaker_interrupting_a']=100
    r=run_equipment(t,P,tmp_path/'rejected')
    assert r['numerically_qualified'] and r['dynamic_checks_passed']
    assert not r['static_checks_passed'] and r['reward']==0 and r['status']=='rejected'
    assert verify_equipment(tmp_path/'rejected',expected_task=t)['verified']

def test_real_dynamic_failure_overrides_static_pass(task,tmp_path):
    t=small(task);t['mandatory_cases'][0]['motors'][0]['minimum_final_speed_pu']=2
    r=run_equipment(t,P,tmp_path/'rejected')
    assert r['static_checks_passed'] and r['numerically_qualified']
    assert not r['dynamic_checks_passed'] and r['reward']==0

def test_cancelled_has_no_training_reward(task,tmp_path):
    r=run_equipment(small(task),P,tmp_path/'cancelled',cancelled=lambda:True)
    assert r['status']=='incomplete' and r['reward'] is None
    assert not r['meets_declared_requirements']
    with pytest.raises(ValueError):verify_equipment(tmp_path/'cancelled')

def test_numerical_failure_never_becomes_rejection(task):
    t,p,static,_=compile_equipment(task,P)
    dynamic=dict(completed=True,numerically_qualified=False,meets_declared_dynamic_requirements=False,cases=[],code_sha256='synthetic-contract-test')
    r=combined_verdict(t,p,static,dynamic)
    assert r['status']=='numerically_unqualified' and r['reward'] is None

def test_cli_verify(real_packet,capsys):
    from power_grid_env.cli import main
    root,_,_=real_packet
    assert main(['verify-equipment-design',str(root)])==0
    assert '"verified": true' in capsys.readouterr().out

def test_passive_background_only(task):
    task['mandatory_cases'][0]['study']['network']['loads'][0]['q_var']={p:-1 for p in 'ABC'}
    with pytest.raises(ValueError,match='passive'):compile_equipment(task,P)

def test_public_and_external_references(task):
    from benchmarks.equipment_sizing_reference import public_checks,fault_reference
    from power_grid_env.electrical_sizing import calculate
    assert all(c['passed'] for c in public_checks(task['sizing'],P))
    for name in ['c95','c16']:
        p=dict(cable_id=name,transformer_id='t250')
        primary=calculate(task['sizing'],p);ref=fault_reference(task['sizing'],p)
        for bus,key in [('source','bus_fault_a'),('load','end_fault_a')]:
            assert ref['rows'][bus]['currents_a']==pytest.approx([primary['values'][key]]*3,rel=2e-6)
            assert 'frequency=50' in ref['rows'][bus]['commands'][-1]

import copy
import cmath
import json
from pathlib import Path
import pytest
from power_grid_env.electrical_sizing import calculate,run_sizing,verify_sizing,SizingTask
from power_grid_env.study_artifacts import seal,encoded

ROOT=Path(__file__).resolve().parents[1]/'cases/electrical-sizing'
@pytest.fixture
def task():return json.loads((ROOT/'task.json').read_text())
P=dict(cable_id='c95',transformer_id='t250')

def test_independent_complex_network(task):
    r=calculate(task,P);v=r['values']
    # Independent complex per-phase equivalent, with explicit hand constants.
    phase=400/(3**.5)
    ztransformer=complex(.0064,(.0256**2-.0064**2)**.5)
    zcable=complex(.025,.008)
    current=complex(80000,-60000)/(3*phase)
    assert v['bus_fault_a']==pytest.approx(abs(phase/ztransformer))
    assert v['end_fault_a']==pytest.approx(abs(phase/(ztransformer+zcable)))
    assert v['cable_drop_percent']==pytest.approx((current*zcable).real/phase*100)
    assert v['load_current_a']==pytest.approx(144.3375672974)
    assert v['derated_ampacity_a']==pytest.approx(191.25)
    assert v['cable_limit_i2t_a2s']==(143*95)**2
    assert r['passes_declared_checks'] and not r['normative_approval']

def test_rejected(task):
    r=calculate(task,dict(cable_id='c16',transformer_id='t250'))
    assert not r['passes_declared_checks']
    assert not r['checks']['ampacity'] and not r['checks']['voltage_drop']

@pytest.mark.parametrize('field,value,check',[
 ('breaker_interrupting_a',1000,'breaker_interrupting'),
 ('grouping_factor',.1,'ampacity'),('clearing_s',2,'transformer_short_time')])
def test_failures(task,field,value,check):
    task['installation'][field]=value
    assert not calculate(task,P)['checks'][check]

def test_worst_cable_fault_not_downstream(task):
    task['cables'][0]['area_mm2']=10
    r=calculate(task,P)
    assert not r['checks']['cable_adiabatic']
    assert r['values']['cable_fault_i2t_a2s']==pytest.approx(r['values']['bus_fault_a']**2*.1)

@pytest.mark.parametrize('mutate',[
 lambda t:t.update(voltage_v=1100),
 lambda t:t['installation'].update(clearing_s=6),
 lambda t:t['installation'].update(grouping_factor=1.1),
 lambda t:t['installation'].update(ambient_c=float('nan')),
 lambda t:t['installation'].update(method=' '),
 lambda t:t['cables'][0]['source'].update(reference=' '),
 lambda t:t['transformers'][0].update(resistance_percent=5),
 lambda t:t['transformers'][0].update(secondary_v=690),
 lambda t:t['cables'].append(copy.deepcopy(t['cables'][0])),
 lambda t:t.update(candidate_reward=1),
])
def test_contract(task,mutate):
    mutate(task)
    with pytest.raises(ValueError):SizingTask.model_validate(task)

def test_unknown(task):
    with pytest.raises(ValueError):calculate(task,dict(cable_id='foreign',transformer_id='t250'))

def test_owner_anchors_and_resealed_corruption(task,tmp_path):
    root=tmp_path/'study';run_sizing(task,P,root)
    assert verify_sizing(root,expected_task=task,expected_selection=P)['verified']
    altered=copy.deepcopy(task);altered['max_drop_percent']=10
    with pytest.raises(ValueError,match='owner task'):verify_sizing(root,expected_task=altered)
    with pytest.raises(ValueError,match='owner selection'):verify_sizing(root,expected_selection=dict(cable_id='c16',transformer_id='t250'))
    result=json.loads((root/'result.json').read_text());result['values']['bus_fault_a']=0
    (root/'result.json').write_bytes(encoded(result));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError,match='result mismatch'):verify_sizing(root)

def test_cli(task,tmp_path,capsys):
    from power_grid_env.cli import main
    for name,code in [('accepted',0),('rejected',2)]:
        out=tmp_path/name
        assert main(['electrical-sizing','--input',str(ROOT/'task.json'),'--proposal',str(ROOT/f'{name}.json'),'--out',str(out)])==code
        assert main(['verify-electrical-sizing',str(out),'--input',str(ROOT/'task.json'),'--proposal',str(ROOT/f'{name}.json')])==code

def test_published_transformer_example(task):
    # Schneider EIG example: 400 kVA / 420 V / 4%, rounded 13.7 kA.
    task['voltage_v']=420
    task['transformers'][0].update(secondary_v=420,rating_kva=400)
    r=calculate(task,P)
    assert r['values']['bus_fault_a']/1000==pytest.approx(13.7,abs=.05)

def test_zero_reactance_and_unity_pf(task):
    task['lagging_power_factor']=1
    task['cables'][0]['x_ohm_km']=0
    task['transformers'][0]['resistance_percent']=4
    r=calculate(task,P)
    assert r['values']['cable_drop_percent']==pytest.approx(1.5625)

def test_overflow_never_publishes(task,tmp_path):
    task['demand_kva']=1e308
    with pytest.raises((ValueError,ArithmeticError)):
        run_sizing(task,P,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()

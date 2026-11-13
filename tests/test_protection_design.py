import copy
import math
from pathlib import Path
import pytest
from power_grid_env.protection_design import CoordinationPlan,timing,feeder_current,screen,run_screen,verify_screen
from power_grid_env.equipment_catalog import run_catalog
from power_grid_env.study_artifacts import read_json,encoded,seal,verify

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def plan():return read_json(ROOT/'cases/protection-screen/settings.json')

@pytest.mark.parametrize('curve,k,alpha',[('standard_inverse',.14,.02),('very_inverse',13.5,1),('extremely_inverse',80,2)])
def test_independent_analytic_curve(plan,curve,k,alpha):
    setting=plan['primary'];setting.update(curve=curve,pickup_a=100,time_multiplier=.2,pickup_tolerance=0,time_tolerance=0,breaker_opening_s=.03)
    expected=.2*k/(10**alpha-1)
    r=timing(1000,setting)
    assert r['earliest_trip_s']==pytest.approx(expected)
    assert r['latest_clear_s']==pytest.approx(expected+.03)

def test_tolerance_corners_and_no_pickup(plan):
    setting=plan['primary'];r=timing(2000,setting)
    assert r['earliest_trip_s']==pytest.approx(.036)
    assert r['latest_clear_s']==pytest.approx(.064)
    assert timing(950,setting)==dict(earliest_trip_s=None,latest_clear_s=None)
    assert timing(1000,setting)['latest_clear_s'] is None
    assert timing(1000,setting)['earliest_trip_s']==pytest.approx(.036)

@pytest.mark.parametrize('current',[float('nan'),float('inf'),-1])
def test_invalid_relay_current(plan,current):
    with pytest.raises(ValueError):timing(current,plan['primary'])

@pytest.mark.parametrize('mutate',[
 lambda p:p.update(fault_positions=[0,.5,.5,1]),
 lambda p:p.update(fault_positions=[0,.5]),
 lambda p:p.update(fault_positions=[0,1,.5]),
 lambda p:p.update(source_voltage_factors=[1,.9]),
 lambda p:p.update(source_voltage_factors=[0,1]),
 lambda p:p['primary'].update(pickup_tolerance=.5),
 lambda p:p['backup'].update(time_tolerance=-1),
 lambda p:p['primary'].update(curve='invented'),
 lambda p:p.update(catalog_manifest_sha256='other'),
 lambda p:p.update(reward=1),
 lambda p:p.update(sampled_start_margin=.9),
])
def test_plan_contract(plan,mutate):
    mutate(plan)
    with pytest.raises(ValueError):CoordinationPlan.model_validate(plan)

@pytest.fixture(scope='module')
def source(tmp_path_factory):
    import os
    if os.environ.get('PGE_PROTECTION_SOURCE'):
        return Path(os.environ['PGE_PROTECTION_SOURCE'])
    c=read_json(ROOT/'cases/equipment-catalog/catalog.json');b=read_json(ROOT/'cases/equipment-catalog/brief.json')
    b['mandatory_cases']=b['mandatory_cases'][:1];case=b['mandatory_cases'][0]
    case['study']['induction'].update(duration_s=.1,step_s=.001,events=[])
    case['motors'][0]['minimum_final_speed_pu']=0
    case['voltage_windows']=[dict(node_id='n1',from_s=0,to_s=.1,minimum_voltage_pu=0,maximum_voltage_pu=2)]
    out=tmp_path_factory.mktemp('source')/'catalog'
    run_catalog(c,b,read_json(ROOT/'cases/equipment-catalog/selection-250.json'),out)
    return out

@pytest.fixture
def bound_plan(plan,source):
    plan['catalog_manifest_sha256']=verify(source);return plan

def test_real_screen(bound_plan,source):
    r=screen(bound_plan,source)
    assert r['passes_declared_screen'] and r['numerically_qualified']
    assert len(r['faults'])==15
    assert r['reward'] is None and not r['closed_loop_protection_qualified']
    first=r['faults'][0]
    assert first['grading_s']==pytest.approx(.225-.064)
    assert first['primary']['latest_clear_s']==pytest.approx(.064)

@pytest.mark.parametrize('setting,field,value,check',[
 ('primary','pickup_a',100,'sampled_start_no_pickup'),
 ('primary','time_multiplier',.2,'sampled_fault_coordination'),
 ('backup','time_multiplier',.05,'sampled_fault_coordination'),
 ('backup','time_multiplier',2,'sampled_fault_coordination'),
 ('primary','pickup_a',100000,'sampled_fault_coordination'),
 ('backup','interrupting_a',100,'sampled_fault_coordination'),
])
def test_bad_settings_rejected(bound_plan,source,setting,field,value,check):
    bound_plan[setting][field]=value;r=screen(bound_plan,source)
    assert not r['passes_declared_screen'] and not r['checks'][check]
    assert r['reward'] is None

def test_feeder_current_includes_aux_and_matches_source_power(source):
    from power_grid_env.industrial_dol import IndustrialDOLCase
    from power_grid_env.topology import compile_topology
    folder=next((source/'equipment'/'dynamic'/'studies').glob('*/grid-0'))
    # The emitted network already contains the actual selected cable/source.
    study=read_json(folder/'study.json')
    task=read_json(source/'equipment'/'dynamic'/'task.json')
    case=copy.deepcopy(task['mandatory_cases'][0]);case['study']=study
    case=IndustrialDOLCase.model_validate(case);report=read_json(folder/'result.json')
    currents=feeder_current(case,report);n=case.study.network;top=compile_topology(n)
    source_bus=top.terminal_bus[n.sources[0].terminal_id]
    for i,current in enumerate(currents):
        p=report['channels'][n.sources[0].id+'.p_w'][i];q=report['channels'][n.sources[0].id+'.q_var'][i]
        v=report['channels'][source_bus+'.v'][i]*n.voltage_levels[0].nominal_ll_v
        assert current==pytest.approx(math.hypot(p,q)/(math.sqrt(3)*v),rel=1e-8,abs=1e-7)

def test_owner_manifest_blocks_other_source(plan,source):
    with pytest.raises(ValueError,match='manifest mismatch'):screen(plan,source)

@pytest.fixture(scope='module')
def portable(source,tmp_path_factory):
    plan=read_json(ROOT/'cases/protection-screen/settings.json');plan['catalog_manifest_sha256']=verify(source)
    out=tmp_path_factory.mktemp('portable')/'screen';run_screen(plan,source,out)
    return out,plan

def test_portable_anchored_verification(portable):
    root,plan=portable
    assert verify_screen(root,expected_plan=plan)['verified']
    changed=copy.deepcopy(plan);changed['primary']['pickup_a']=1100
    with pytest.raises(ValueError,match='owner protection'):verify_screen(root,expected_plan=changed)

@pytest.mark.parametrize('field',['reward','passes_declared_screen','faults'])
def test_resealed_tampering(portable,tmp_path,field):
    import shutil
    root,_=portable;out=tmp_path/'copy';shutil.copytree(root,out)
    r=read_json(out/'result.json')
    if field=='faults':r['faults'][0]['current_a']=0
    elif field=='reward':r['reward']=1
    else:r[field]=False
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='evidence differs'):verify_screen(out)

def test_output_cannot_invalidate_source(bound_plan,source):
    before=verify(source)
    with pytest.raises(ValueError,match='outside source'):run_screen(bound_plan,source,source/'screen')
    assert verify(source)==before

def test_cli(portable,capsys):
    from power_grid_env.cli import main
    root,_=portable
    assert main(['verify-protection-screen',str(root)])==0
    assert '"verified": true' in capsys.readouterr().out

def test_external_fault_matrix_and_resealed_summary(source,plan,tmp_path):
    from benchmarks.protection_design_acceptance import run,verify_matrix
    root=tmp_path/'matrix';r=run(source,plan,root)
    assert r['passed'] and r['external_fault_solutions']==15
    assert verify_matrix(root)['verified']
    summary=read_json(root/'summary.json');summary['closed_loop_protection_qualified']=True
    (root/'summary.json').write_bytes(encoded(summary));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError,match='summary differs'):verify_matrix(root)

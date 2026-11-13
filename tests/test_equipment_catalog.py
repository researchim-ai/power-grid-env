import copy
from pathlib import Path
import pytest
from power_grid_env.equipment_catalog import EquipmentCatalog,compile_catalog,run_catalog,verify_catalog
from power_grid_env.study_artifacts import read_json,encoded,seal

ROOT=Path(__file__).resolve().parents[1]/'cases/equipment-catalog'
@pytest.fixture
def catalog():return read_json(ROOT/'catalog.json')
@pytest.fixture
def brief():return read_json(ROOT/'brief.json')
P=dict(cable_id='nexans-084193001',transformer_id='trihal36-250')

def short(brief):
    b=copy.deepcopy(brief);b['mandatory_cases']=b['mandatory_cases'][:1]
    c=b['mandatory_cases'][0];c['study']['induction'].update(duration_s=.1,step_s=.001,events=[])
    c['motors'][0]['minimum_final_speed_pu']=0
    c['voltage_windows']=[dict(node_id='n1',from_s=0,to_s=.1,minimum_voltage_pu=0,maximum_voltage_pu=2)]
    return b

def test_exact_derivation_and_unchanged_inputs(catalog,brief):
    old=(copy.deepcopy(catalog),copy.deepcopy(brief))
    _,_,_,task,receipt=compile_catalog(catalog,brief,P)
    assert (catalog,brief)==old
    assert task.sizing.cables[0].r_operating_ohm_km==pytest.approx(.258399015)
    assert [t.resistance_percent for t in task.sizing.transformers]==pytest.approx([1.6,1.425])
    assert task.sizing.cables[0].ampacity_a==298
    assert not receipt['manufacturer_authenticated'] and receipt['owner_assumptions_present']
    rows={r['field']:r for r in receipt['lineage'] if r['equipment_id']=='trihal36-250'}
    assert rows['short_time_a']['evidence_kind']=='owner_assumption'
    assert rows['load_loss_w']['evidence_kind']=='manufacturer'
    assert task.mandatory_cases==__import__('power_grid_env.equipment_catalog',fromlist=['CatalogBrief']).CatalogBrief.model_validate(brief).mandatory_cases

@pytest.mark.parametrize('mutate',[
 lambda c:c['cables'][0]['area_mm2'].update(unit='A'),
 lambda c:c['cables'][0]['dc_r20_ohm_km'].update(document_id='missing'),
 lambda c:c['cables'][0]['ampacity_air30_a'].update(value=0),
 lambda c:c['cables'][0]['area_mm2'].update(document_id='owner'),
 lambda c:c['assumptions']['ac_dc_resistance_factor'].update(value=.99),
 lambda c:c['assumptions']['conductor_temperature_c'].update(value=100),
 lambda c:c['assumptions']['grouping_factor'].update(value=1.1),
 lambda c:c['assumptions']['copper_alpha_per_c'].update(document_id='nexans'),
 lambda c:c['transformers'][0]['load_loss_w'].update(value=1000000),
 lambda c:c['documents'][0].update(locator=' '),
 lambda c:c['documents'].append(copy.deepcopy(c['documents'][0])),
 lambda c:c['cables'].append(copy.deepcopy(c['cables'][0])),
 lambda c:c['air_temperature_factors'].append(copy.deepcopy(c['air_temperature_factors'][0])),
 lambda c:c['air_temperature_factors'][0]['factor'].update(value=.99),
 lambda c:c['air_temperature_factors'][1]['factor'].update(document_id='unknown'),
 lambda c:c['cables'][0]['area_mm2'].update(value=float('nan')),
 lambda c:c.update(reward=1),
])
def test_invalid_extracts(catalog,mutate):
    mutate(catalog)
    with pytest.raises(ValueError):EquipmentCatalog.model_validate(catalog)

def test_discrete_40_degree_row(catalog,brief):
    catalog['assumptions']['temperature_factor']=copy.deepcopy(catalog['air_temperature_factors'][1]['factor'])
    brief['installation'].update(ambient_c=40,temperature_factor=.91)
    _,_,_,task,r=compile_catalog(catalog,brief,P)
    assert r['installation_lookup']['ambient_c']==40
    assert task.sizing.installation.temperature_factor==.91
    assert not r['installation_lookup']['interpolation']

@pytest.mark.parametrize('change', ['ambient','method','grouping','other','factor','document'])
def test_installation_mismatch(catalog,brief,change):
    if change=='ambient':brief['installation']['ambient_c']=35
    if change=='method':brief['installation']['method']='buried'
    if change=='grouping':
        brief['installation']['grouping_factor']=.8;catalog['assumptions']['grouping_factor']['value']=.8
    if change=='other':
        brief['installation']['other_factor']=.9;catalog['assumptions']['other_factor']['value']=.9
    if change=='factor':brief['installation']['temperature_factor']=.91
    if change=='document':catalog['assumptions']['temperature_factor']['document_id']='owner'
    with pytest.raises(ValueError):compile_catalog(catalog,brief,P)

def test_unknown_choice_before_output(catalog,brief,tmp_path):
    with pytest.raises(ValueError):run_catalog(catalog,brief,dict(cable_id='foreign',transformer_id='trihal36-250'),tmp_path/'bad')
    assert not (tmp_path/'bad').exists()

@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    c=read_json(ROOT/'catalog.json');b=short(read_json(ROOT/'brief.json'))
    root=tmp_path_factory.mktemp('catalog')/'study';r=run_catalog(c,b,P,root)
    return root,c,b,r

def test_real_passport_study(packet):
    root,c,b,r=packet
    assert r['status']=='accepted' and r['reward']==1
    assert verify_catalog(root,expected_catalog=c,expected_brief=b,expected_selection=P)['verified']

def test_owner_anchors(packet):
    root,c,b,_=packet
    c=copy.deepcopy(c);c['cables'][0]['ampacity_air30_a']['value']=300
    with pytest.raises(ValueError,match='owner catalog'):verify_catalog(root,expected_catalog=c)
    b=copy.deepcopy(b);b['max_drop_percent']=10
    with pytest.raises(ValueError,match='owner brief'):verify_catalog(root,expected_brief=b)
    with pytest.raises(ValueError,match='owner selection'):verify_catalog(root,expected_selection=dict(cable_id=P['cable_id'],transformer_id='trihal36-400'))

@pytest.mark.parametrize('file,edit',[
 ('compilation.json',lambda x:x['derived'][0].update(value=0)),
 ('compilation.json',lambda x:x['lineage'][0].update(evidence_kind='owner_assumption')),
 ('result.json',lambda x:x.update(reward=0)),
])
def test_resealed_corruption(packet,tmp_path,file,edit):
    import shutil
    root,_,_,_=packet;out=tmp_path/'copy';shutil.copytree(root,out)
    data=read_json(out/file);edit(data);(out/file).write_bytes(encoded(data));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_catalog(out)

def test_cancelled_not_sft_label(catalog,brief,tmp_path):
    r=run_catalog(catalog,short(brief),P,tmp_path/'cancelled',cancelled=lambda:True)
    assert r['status']=='incomplete' and r['reward'] is None

def test_cli_verify(packet,capsys):
    from power_grid_env.cli import main
    root,_,_,_=packet
    assert main(['verify-catalog-design',str(root),'--catalog',str(ROOT/'catalog.json')])==0
    assert '"verified": true' in capsys.readouterr().out

def test_no_frequency_extrapolation(catalog,brief,tmp_path):
    for c in brief['mandatory_cases']:c['study']['network']['frequency_hz']=60
    with pytest.raises(ValueError,match='50 Hz'):run_catalog(catalog,brief,P,tmp_path/'bad-frequency')
    assert not (tmp_path/'bad-frequency').exists()

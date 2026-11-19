import copy
import json
import pytest
from benchmarks.grid_editor import Draft
from benchmarks.studio_exchange import import_model,export_model,package,TASK,PACKAGE,evaluate_task

@pytest.fixture
def network():
    return dict(schema='power-grid-env.grid-editor/v5',profile='ac_power_flow',nominal_ll_v=400.,source_node='n0',source_r_ohm=.001,source_x_ohm=.005,duration_s=1.,step_s=.0025,
        nodes=[dict(id='n0',nominal_ll_v=400.,x=100.,y=200.),dict(id='n1',nominal_ll_v=400.,x=500.,y=200.)],
        lines=[dict(id='l1',from_node='n0',to_node='n1',length_m=100.,r_ohm_km=.1,x_ohm_km=.05,rating_a=200.)],motors=[],loads=[dict(id='load1',node_id='n1',p_kw=20.,q_kvar=5.,model='constant_pq')],events=[],transformers=[],switches=[],generators=[],fault=dict(node_id='n1',r_ohm=.001,x_ohm=0.))

@pytest.fixture
def packet(network):
    return dict(schema=PACKAGE,project=network,task=dict(schema=TASK,id='voltage_check',title='Проверить режим',criteria=dict(minimum_voltage_pu=.95,maximum_branch_loading_pct=100.,all_loads_energized=True)))

@pytest.mark.parametrize('format',['package_json','studio_csv'])
def test_project_task_and_parameters_roundtrip(packet,format):
    expected=package(packet);out=export_model(format,packet);result=import_model(format,out['content'])
    assert result['package']==expected and result['report']['task_preserved']
    assert result['report']['lossless'] and not result['report']['requires_acknowledgement']

def test_project_export_cannot_silently_drop_task(packet):
    with pytest.raises(ValueError,match='lose task'):export_model('project_json',packet)

def test_duplicate_json_csv_and_hidden_task_fields_rejected(packet):
    with pytest.raises(ValueError,match='duplicate JSON'):import_model('package_json','{"schema":1,"schema":2}')
    data=export_model('studio_csv',packet)['content'];lines=data.splitlines()
    with pytest.raises(ValueError,match='duplicate CSV'):import_model('studio_csv',data+'\n'+lines[1])
    packet['task']['gold_answer']='secret'
    with pytest.raises(ValueError,match='no hidden answer'):package(packet)

def test_real_stationary_task_evaluation(packet):
    r=evaluate_task(packet)
    assert r['passed'] and r['minimum_voltage_pu']>.95
    packet['project']['lines'][0]['rating_a']=1.
    assert 'branch_loading' in evaluate_task(packet)['violations']

MATPOWER="""function mpc = small_case
mpc.version = '2';
mpc.baseMVA = 1;
mpc.bus = [1 3 0 0 0 0 1 1 0 .4 1 1.1 .9; 2 1 .02 .005 0 0 1 1 0 .4 1 1.1 .9];
mpc.gen = [1 0 0 10 -10 1 1 1 10 -10];
mpc.branch = [1 2 .0625 .03125 0 .1 .1 .1 0 0 1 -360 360];
"""

def test_matpower_units_and_conversion_provenance_survive_native_export():
    r=import_model('matpower_v2',MATPOWER);d=r['package']['project']
    assert d['nominal_ll_v']==400 and d['loads'][0]['p_kw']==20
    assert d['lines'][0]['r_ohm_km']==pytest.approx(.01)
    assert r['report']['requires_acknowledgement'] and not r['report']['lossless']
    out=export_model('package_json',r['package'])
    restored=import_model('package_json',out['content'])
    assert restored['report']['requires_acknowledgement'] and restored['package']==r['package']
    assert import_model('studio_csv',export_model('studio_csv',r['package'])['content'])['package']==r['package']

@pytest.mark.parametrize('text',[
    MATPOWER+"system('touch /tmp/evil');",MATPOWER+'mpc.bus = [1];',
    MATPOWER.replace('.0625 .03125 0','.0625 .03125 .001'),
    MATPOWER.replace('1 3 0 0 0 0','1 3 0 0 1 0'),
    MATPOWER.replace('2 1 .02','2 2 .02'),
    MATPOWER.replace('.1 .1 .1 0 0 1','.1 .1 .1 1.02 0 1'),
    MATPOWER.replace('.4 1 1.1 .9','110 1 1.1 .9'),
])
def test_unsupported_matpower_is_rejected_not_flattened(text):
    with pytest.raises(ValueError):import_model('matpower_v2',text)

def test_csv_provenance_and_unknown_sections_fail_closed(packet):
    data=export_model('studio_csv',packet)['content']
    with pytest.raises(ValueError,match='unknown CSV'):import_model('studio_csv',data+'unknown,0,x,1\n')
    packet['conversion']=dict(source_format='matpower_v2',input_sha256='x',warnings=[])
    with pytest.raises(ValueError,match='provenance'):package(packet)

def test_published_json_schema_is_valid_and_rejects_private_task_fields(packet):
    from jsonschema import Draft202012Validator
    from benchmarks.studio_exchange import package_schema
    schema=package_schema();Draft202012Validator.check_schema(schema)
    validator=Draft202012Validator(schema);validator.validate(package(packet))
    packet['task']['hidden_answer']='not_allowed'
    assert list(validator.iter_errors(packet))

import copy
import shutil
import xml.etree.ElementTree as ET
import pytest
from benchmarks.network_construction_cases import example,proposal
from power_grid_env.network_design import compile_network,run_network_design,verify_network_design,verdict
from power_grid_env.network_design_view import observation,single_line_svg,export_view
from power_grid_env.study_artifacts import read_json,encoded,seal


def test_route_choice_changes_actual_network_and_shared_current():
    original=example(short=True);choice=proposal('chain');before=copy.deepcopy((original,choice))
    t,p,plan,c=compile_network(original,choice)
    assert (original,choice)==before
    assert {l.id for l in plan.cases[0].study.network.lines}=={'c01','c12'}
    rows={r['corridor_id']:r for r in c['static']['branches']}
    assert rows['c01']['downstream_kva']==152 and rows['c12']['downstream_kva']==75
    assert rows['c01']['nominal_current_a']>2*rows['c12']['nominal_current_a']
    assert plan.cases[0].motors==t.mandatory_cases[0].motors
    assert plan.cases[0].study.induction.motors==t.mandatory_cases[0].study.induction.motors
    assert rows['c01']['upstream_fault_a']>next(n['source_fault_a'] for n in c['static']['nodes'] if n['node_id']=='n1')


def test_long_route_rejected_without_changing_owner_criteria():
    t,p,plan,c=compile_network(example(short=True),proposal('long'))
    assert not c['static']['passes_declared_checks'] and not c['static']['checks']['n2.projected_drop']
    assert t.maximum_projected_drop_percent==5
    assert plan.cases[0].voltage_windows==t.mandatory_cases[0].voltage_windows


def test_proposal_order_is_canonical():
    a=proposal();b=copy.deepcopy(a);b['branches'].reverse()
    x,y=[compile_network(example(short=True),p) for p in (a,b)]
    assert x[1].model_dump(mode='json')==y[1].model_dump(mode='json')
    assert x[2].model_dump(mode='json')==y[2].model_dump(mode='json') and x[3]==y[3]


def test_transformer_choice_changes_compiled_thevenin_impedance():
    a=compile_network(example(short=True),proposal(transformer='trihal36-250'))
    b=compile_network(example(short=True),proposal(transformer='trihal36-400'))
    za=a[2].cases[0].study.network.sources[0];zb=b[2].cases[0].study.network.sources[0]
    assert za.r_ohm==pytest.approx(.01024) and zb.r_ohm==pytest.approx(.0057)
    assert abs(complex(za.r_ohm,za.x_ohm))>abs(complex(zb.r_ohm,zb.x_ohm))


@pytest.mark.parametrize('bad',['unknown_route','unknown_cable','unknown_transformer','duplicate','disconnected','cycle','extra_criteria','alter_load'])
def test_candidate_cannot_leave_catalog_or_edit_requirements(bad,tmp_path):
    t,p=example(short=True),proposal()
    if bad=='unknown_route':p['branches'][0]['corridor_id']='missing'
    elif bad=='unknown_cable':p['branches'][0]['cable_id']='missing'
    elif bad=='unknown_transformer':p['transformer_id']='missing'
    elif bad=='duplicate':p['branches'][1]=copy.deepcopy(p['branches'][0])
    elif bad=='disconnected':p['branches']=[dict(corridor_id=k,cable_id='nexans-084193001') for k in ('c12','long12')]
    elif bad=='cycle':p['branches'].append(dict(corridor_id='c12',cable_id='nexans-084193001'))
    elif bad=='extra_criteria':p['maximum_projected_drop_percent']=100
    else:p['motors']=[]
    with pytest.raises(ValueError):run_network_design(t,p,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


@pytest.mark.parametrize('bad',['demand','missing_node','phase','frequency','voltage','factor','ambient','method','current_model','generated_collision','terminal'])
def test_unsupported_owner_profile_fails_before_artifacts(bad,tmp_path):
    t,p=example(short=True),proposal();s=t['mandatory_cases'][0]['study']
    if bad=='demand':t['node_demand_kva']['n1']=75
    elif bad=='missing_node':t['node_demand_kva'].pop('n2')
    elif bad=='phase':s['network']['connectivity_nodes'][1]['phases']=['A']
    elif bad=='frequency':s['network']['frequency_hz']=60
    elif bad=='voltage':s['network']['sources'][0]['ll_v']=380
    elif bad=='factor':t['corridors'][0]['installation']['temperature_factor']=.9
    elif bad=='ambient':t['corridors'][0]['installation']['ambient_c']=35
    elif bad=='method':t['corridors'][0]['installation']['method']='buried'
    elif bad=='current_model':s['network']['loads'][0]['model']='constant_pq'
    elif bad=='terminal':s['network']['loads'][0]['terminal_id']='missing'
    else:
        t['corridors'][0]['id']='source';p['branches'][0]['corridor_id']='source'
    with pytest.raises(ValueError):run_network_design(t,p,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


@pytest.fixture(scope='module')
def accepted(tmp_path_factory):
    root=tmp_path_factory.mktemp('constructed')/'run'
    t,p=example(short=True),proposal();r=run_network_design(t,p,root)
    assert r['meets_declared_requirements']
    return root,t,p


def test_owner_anchored_verifier_recomputes_whole_decision(accepted):
    root,t,p=accepted;checked=verify_network_design(root,expected_task=t,expected_proposal=p)
    assert checked['owner_task_anchored'] and checked['submitted_proposal_anchored'] and checked['reward']==1.
    other=copy.deepcopy(t);other['maximum_projected_drop_percent']=10
    with pytest.raises(ValueError,match='owner task'):verify_network_design(root,expected_task=other,expected_proposal=p)
    with pytest.raises(ValueError,match='proposal'):verify_network_design(root,expected_task=t,expected_proposal=proposal('chain'))


@pytest.mark.parametrize('mutation',['reward','completion','static'])
def test_resealed_false_verdict_rejected(accepted,tmp_path,mutation):
    root,t,p=accepted;copyroot=tmp_path/'copy';shutil.copytree(root,copyroot)
    r=read_json(copyroot/'result.json')
    if mutation=='reward':r['reward']=0.
    elif mutation=='completion':r['completed']=False
    else:r['static_checks_passed']=False
    (copyroot/'result.json').write_bytes(encoded(r));(copyroot/'manifest.json').unlink();seal(copyroot)
    with pytest.raises(ValueError,match='verdict'):verify_network_design(copyroot,expected_task=t,expected_proposal=p)


def test_static_rejection_still_runs_required_dynamic_grids(tmp_path):
    root=tmp_path/'long';r=run_network_design(example(short=True),proposal('long'),root)
    assert r['completed'] and r['numerically_qualified'] and not r['meets_declared_requirements'] and r['reward']==0.
    assert len(list((root/'studies').glob('*/grid-*/result.json')))==3


def test_cancellation_never_becomes_qualified_rejection(tmp_path):
    r=run_network_design(example(short=True),proposal(),tmp_path/'cancelled',cancelled=lambda:True)
    assert r['status']=='incomplete' and r['reward'] is None and not r['meets_declared_requirements']


def test_public_single_line_contains_no_result_or_future_labels(tmp_path):
    t,p=example(short=True),proposal();data=observation(t,p);svg=single_line_svg(t,p)
    assert len(data['nodes'])==3 and len(data['branches'])==2
    assert not {'reward','verdict','events','checks','event_log','expected'}&data.keys()
    ET.fromstring(svg)
    assert svg==single_line_svg(t,p)
    r=export_view(t,p,tmp_path/'view')
    assert r['public_observation'] and not r['contains_verdict'] and not r['contains_future_events']


def test_public_SVG_escapes_owner_motor_names():
    t,p=example(short=True),proposal();name='<script>alert(1)</script>'
    t['mandatory_cases'][0]['study']['induction']['motors'][0]['id']=name
    t['mandatory_cases'][0]['motors'][0]['motor_id']=name
    svg=single_line_svg(t,p);ET.fromstring(svg)
    assert '<script>' not in svg and '&lt;script&gt;' in svg


def test_compile_and_view_CLI(tmp_path,capsys):
    from power_grid_env.cli import main
    t,p=example(short=True),proposal()
    for name,data in [('task',t),('proposal',p)]:(tmp_path/(name+'.json')).write_bytes(encoded(data))
    common=['--input',str(tmp_path/'task.json'),'--proposal',str(tmp_path/'proposal.json')]
    assert main(['compile-network-design',*common,'--out',str(tmp_path/'compiled.json')])==0
    assert main(['network-design-view',*common,'--out',str(tmp_path/'view')])==0
    assert main(['network-design',*common,'--out',str(tmp_path/'run')])==0
    assert main(['verify-network-design',str(tmp_path/'run'),*common])==0

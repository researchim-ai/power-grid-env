import copy
import os
from pathlib import Path
import pytest
from tests.test_design_study import task
from power_grid_env.design_study import compile_design,run_design,verify_design,FeederDesignTask
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    owner=task();owner['mandatory_cases'][1]['motors'][0]['minimum_final_speed_pu']=0
    for case in owner['mandatory_cases']:
        case['study']['induction']['step_s']=.001
        case['study']['induction']['motors'][0]['thermal']=dict(time_constant_s=1.,trip_level=100.,initial_level=.2)
    root=tmp_path_factory.mktemp('qualified-design')/'accepted'
    result=run_design(owner,{'option_id':'reference'},root)
    assert result['status']=='accepted'
    return root,owner


def test_real_three_grids_and_owner_anchor(packet):
    root,owner=packet
    r=verify_design(root,expected_task=owner,expected_proposal={'option_id':'reference'})
    assert r['verified'] and r['owner_task_anchored'] and r['submitted_proposal_anchored']
    assert r['numerically_qualified'] and r['reward']==1 and not r['qualified_electrical_design']
    assert len(list((root/'studies').glob('*/grid-*/result.json')))==6
    for case in r['cases']:assert case['decision_stable'] and len(case['grid_decisions'])==3


def test_base_refinement_preserves_owner_grid_and_never_changes_criteria():
    owner=task();original=copy.deepcopy(owner)
    _,_,plan=compile_design(owner,{'option_id':'reference'})
    assert owner==original
    for case,source in zip(plan.cases,owner['mandatory_cases']):
        ratio=source['study']['induction']['step_s']/case.study.induction.step_s
        assert ratio==2 and case.study.induction.step_s<=1/(8*case.study.network.frequency_hz)
        assert case.motors[0].minimum_final_speed_pu==source['motors'][0]['minimum_final_speed_pu']


def test_cancelled_design_is_not_a_negative_training_label(tmp_path):
    root=tmp_path/'cancelled';r=run_design(task(),{'option_id':'reference'},root,cancelled=lambda:True)
    assert r['status']=='incomplete' and not r['completed'] and r['reward'] is None
    assert not r['meets_declared_dynamic_requirements'] and verify(root)
    with pytest.raises(ValueError,match='complete'):verify_design(root)


@pytest.mark.parametrize('defect',['missing_trace','double_cage'])
def test_unsupported_audit_profile_rejects_before_execution(defect):
    owner=task()
    if defect=='missing_trace':owner['mandatory_cases'][0]['study']['induction']['event_trace']='summary'
    else:owner['mandatory_cases'][0]['study']['induction']['motors'][0].update(model='double_cage',rotor2_r_pu=.07,rotor2_x_pu=.12)
    with pytest.raises(ValueError):FeederDesignTask.model_validate(owner)


def reseal(root):
    (root/'studies'/'manifest.json').unlink();seal(root/'studies')
    (root/'manifest.json').unlink();seal(root)


@pytest.mark.parametrize('defect',['audit_limits','grid','current','thermal','audit','summary','verdict','scope','task_anchor','proposal_anchor'])
def test_resealed_design_evidence_cannot_override_physics_or_owner(packet,tmp_path,defect):
    import shutil
    original,owner=packet;root=tmp_path/'forged';shutil.copytree(original,root)
    if defect=='task_anchor':
        changed=copy.deepcopy(owner);changed['mandatory_cases'][0]['motors'][0]['maximum_sampled_current_pu']=101
        with pytest.raises(ValueError,match='owner task'):verify_design(root,expected_task=changed)
        return
    if defect=='proposal_anchor':
        with pytest.raises(ValueError,match='proposal anchor'):verify_design(root,expected_proposal={'option_id':'other'})
        return
    if defect=='audit_limits':
        p=root/'studies'/'input.json';r=read_json(p);r['limits']['algebra_pu']=1e-6
    elif defect=='grid':
        p=root/'studies'/'start'/'grid-2'/'study.json';r=read_json(p);r['induction']['step_s']*=2
    elif defect in {'current','thermal'}:
        p=root/'studies'/'start'/'grid-2'/'result.json';r=read_json(p)
        if defect=='current':r['channels']['m.current_pu'][3]+=1
        else:r['channels']['m.thermal_level']=[0.]*len(r['time_s'])
    elif defect=='audit':
        p=root/'studies'/'start'/'grid-2'/'audit.json';r=read_json(p);r['passed']=False
    elif defect=='summary':
        p=root/'studies'/'summary.json';r=read_json(p);r['cases']=[]
    else:
        p=root/'result.json';r=read_json(p)
        if defect=='verdict':r['reward']=0
        else:r['qualified_electrical_design']=True
    p.write_bytes(encoded(r));reseal(root)
    with pytest.raises(ValueError):verify_design(root,expected_task=owner,expected_proposal={'option_id':'reference'})


def test_structural_topology_key_ignores_names():
    from power_grid_env.design_corpus import tasks,topology_key
    items=tasks();keys=[i['topology_sha256'] for i in items]
    assert len(set(keys))==6 and {i['split'] for i in items}=={'train','dev','test'}
    network=items[-1]['task'].mandatory_cases[0].study.network.model_copy(deep=True)
    for node in network.connectivity_nodes:node.id+='-renamed'
    for terminal in network.terminals:terminal.connectivity_node_id+='-renamed'
    network.connectivity_nodes.reverse();network.lines.reverse()
    assert topology_key(network)==keys[-1]


@pytest.fixture(scope='module')
def corpus():
    value=os.environ.get('PGE_DESIGN_CORPUS')
    if not value:pytest.skip('set PGE_DESIGN_CORPUS for the qualified owner corpus')
    return Path(value)


@pytest.fixture(scope='module')
def checked_corpus(corpus):
    from power_grid_env.design_corpus import verify_corpus
    return verify_corpus(corpus)


def test_whole_corpus_has_108_real_grids_and_qualified_rejections(corpus,checked_corpus):
    assert checked_corpus['tasks']==6 and checked_corpus['options']==12
    assert checked_corpus['splits']=={'train':2,'dev':1,'test':3}
    assert len(list(corpus.glob('tasks/*/opt-*/studies/*/grid-*/result.json')))==108
    data=read_json(corpus/'corpus.json')
    assert sum(not r['feasible'] for r in data['tasks'])==2
    assert sum(o['reward']==1 for r in data['tasks'] for o in r['options'])==4


@pytest.fixture
def verified_api(corpus,checked_corpus,monkeypatch):
    # Physics is genuinely recomputed once above. Contract tests reuse exactly
    # that immutable manifest, not invented labels or a mocked simulation.
    import power_grid_env.design_corpus as api
    def guarded(root):
        assert Path(root).resolve()==corpus.resolve()
        assert verify(root)==checked_corpus['manifest_sha256']
        return checked_corpus
    monkeypatch.setattr(api,'verify_corpus',guarded)
    return api


def test_prompt_exports_exclude_answers_and_sft_only_uses_train(verified_api,corpus,tmp_path):
    import json
    api=verified_api
    api.export_dataset(corpus,tmp_path/'prompts.jsonl',split='test')
    rows=[json.loads(line) for line in (tmp_path/'prompts.jsonl').read_text().splitlines()]
    assert len(rows)==3 and all(set(r)=={'task_id','task'} for r in rows)
    assert not any('feasible' in r or 'reward' in r or 'options' in r for r in rows)
    api.export_dataset(corpus,tmp_path/'sft.jsonl',kind='sft')
    rows=[json.loads(line) for line in (tmp_path/'sft.jsonl').read_text().splitlines()]
    assert len(rows)==2
    answers=[json.loads(r['messages'][-1]['content']) for r in rows]
    assert {'no_feasible_option':True} in answers and {'option_id':'opt-a'} in answers
    with pytest.raises(ValueError,match='held-out'):api.export_dataset(corpus,tmp_path/'leak.jsonl',kind='sft',split='test')
    assert not (tmp_path/'leak.jsonl').exists()


def oracle_predictions(corpus):
    rows=read_json(corpus/'corpus.json')['tasks']
    return [dict(task_id=r['id'],proposal=next((dict(option_id=o['option_id']) for o in r['options'] if o['reward']==1),dict(no_feasible_option=True))) for r in rows if r['split']=='test']


def test_evaluation_rewards_real_choices_and_whole_catalog_abstention(verified_api,corpus,tmp_path):
    api=verified_api;predictions=oracle_predictions(corpus)
    assert api.evaluate(corpus,predictions,tmp_path/'oracle')['mean_reward']==1
    for item in predictions:item['proposal']={'no_feasible_option':True}
    assert api.evaluate(corpus,predictions,tmp_path/'abstain')['mean_reward']==pytest.approx(1/3)
    for item in predictions:item['proposal']={'option_id':'opt-a'}
    assert api.evaluate(corpus,predictions,tmp_path/'constant')['mean_reward']==0


@pytest.mark.parametrize('defect',['missing','duplicate','foreign_task','unknown_option','extra_field','fake_abstention'])
def test_bad_predictions_cannot_earn_reward(verified_api,corpus,tmp_path,defect):
    api=verified_api;p=oracle_predictions(corpus)
    if defect=='missing':p.pop()
    elif defect=='duplicate':p[1]=p[0]
    elif defect=='foreign_task':p[0]['task_id']='design-radial-standard'
    elif defect=='unknown_option':p[0]['proposal']={'option_id':'made-up'}
    elif defect=='extra_field':p[0]['proposal']['reward']=1
    else:p[0]['proposal']={'no_feasible_option':False}
    with pytest.raises(ValueError):api.evaluate(corpus,p,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_exports_cannot_modify_the_sealed_corpus(verified_api,corpus):
    with pytest.raises(ValueError,match='outside'):verified_api.export_dataset(corpus,corpus/'public.jsonl')
    with pytest.raises(ValueError,match='outside'):verified_api.evaluate(corpus,oracle_predictions(corpus),corpus/'evaluation')


def test_changed_owner_task_in_resealed_corpus_is_rejected(corpus,tmp_path):
    import shutil
    from power_grid_env.design_corpus import verify_corpus
    root=tmp_path/'changed';shutil.copytree(corpus,root)
    p=root/'tasks'/'design-radial-standard'/'task.json';r=read_json(p);r['mandatory_cases'][0]['motors'][0]['minimum_final_speed_pu']=0
    p.write_bytes(encoded(r));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError,match='owner task'):verify_corpus(root)

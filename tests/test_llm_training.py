import copy
import json
from pathlib import Path
import shutil

import pytest

from power_grid_env.llm_tasks import LLMGenerationPlan,TopologyFamily,generate_task
from power_grid_env.llm_training import (LLMEpisode,CorpusVerifier,strict_json,score_plan,score_group,
    teacher_episode,initial_context,rollout_tools,generate_corpus,verify_corpus,export_public_dataset,_read_lines)
from power_grid_env.llm_fit import TrainingRecipe,checked_rows
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,export_zip,extract_zip


@pytest.fixture(scope='module')
def task():
    plan=LLMGenerationPlan();return generate_task(plan,plan.families[0],0)


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root=tmp_path_factory.mktemp('llm')/'corpus';generate_corpus({},root);return root


@pytest.mark.parametrize('raw',['{}','[]','null','{"actions":[],"reward":1}',
    '{"actions":[],"actions":[]}','{"actions":[{"kind":"hold","reward":1}]}',
    '{"actions":[{"kind":"shell","command":"anything"}]}',
    '{"actions":[{"kind":"switch","equipment_id":"unknown","command":"open"}]}',
    '{"actions":NaN}','{"actions":Infinity}','{"actions":1e999}', 'x'*32769])
def test_invalid_completion_never_gets_format_reward(task,raw):
    result=score_plan(task,raw)
    assert result['status']=='invalid_completion' and result['reward']==-1


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":1e999}'])
def test_json_boundary_rejects_ambiguity(raw):
    with pytest.raises(ValueError):strict_json(raw)


def test_topology_signature_ignores_identifiers_and_sibling_order():
    a=TopologyFamily(id='one',split='train',parents=[-1,0,0,1,2,3])
    b=TopologyFamily(id='two',split='test',parents=[-1,0,0,2,1,4])
    assert a.topology_group()==b.topology_group()
    plan=LLMGenerationPlan().model_dump();plan['families']=[a.model_dump(),b.model_dump(),plan['families'][2]]
    with pytest.raises(ValueError,match='isomorphic'):LLMGenerationPlan.model_validate(plan)


@pytest.mark.parametrize('change',[dict(variants_per_family=65),dict(variants_per_family=0),dict(seed=-1),dict(unknown=True)])
def test_bounded_generation(change):
    with pytest.raises(ValueError):LLMGenerationPlan.model_validate(change)


def test_deterministic_private_generation_and_public_noninterference(task):
    plan=LLMGenerationPlan();assert generate_task(plan,plan.families[0],0)==task
    changed=task.model_copy(deep=True);changed.scenario.hidden_canary='OTHER_PRIVATE_CANARY'
    a=initial_context(task)[2];b=initial_context(changed)[2]
    assert a==b
    text=encoded(a).decode()
    assert task.scenario.hidden_canary not in text and 'privileged' not in text and 'fault_r_ohm' not in text


@pytest.mark.parametrize('variant',[0,1,2])
def test_feasible_teacher_and_real_constraints(variant):
    plan=LLMGenerationPlan();task=generate_task(plan,plan.families[0],variant)
    teacher=teacher_episode(task,'heuristic')
    assert teacher['score']['feasible']
    assert teacher['score']['restored']==(variant==0)
    assert score_plan(task,encoded(teacher['plan']).decode())==teacher['score']
    assert teacher['score']['evaluation']['energy_log_matches_report']


def test_no_early_stop_reward_hacking(task):
    short=score_plan(task,'{"actions":[]}')
    holds=score_plan(task,encoded(dict(actions=[dict(kind='hold')]*8)).decode())
    assert short==holds
    assert teacher_episode(task,'heuristic')['score']['reward']>short['reward']


def test_terminal_suffix_and_unknown_suffix_rejected(task):
    plan=teacher_episode(task,'heuristic')['plan']
    plan['actions'].append(dict(kind='switch',equipment_id=task.network.switches[0].id,command='close'))
    assert score_plan(task,encoded(plan).decode())['reward']==-1
    plan['actions'][-1]['equipment_id']='unknown'
    assert score_plan(task,encoded(plan).decode())['reason']=='unknown_public_switch'


def test_what_if_cannot_change_real_episode(task):
    first=LLMEpisode(task);second=LLMEpisode(task)
    before=copy.deepcopy(first._engine.trace)
    first.call('simulate_plan',dict(actions=[dict(kind='hold')]))
    assert first._engine.trace==before
    assert first.call('observe',{})==second.call('observe',{})
    assert first.finish()==second.finish()


def test_observations_are_deep_copied_and_tools_bounded(task):
    episode=LLMEpisode(task);observation=episode.call('observe',{});observation.clear()
    assert episode.call('observe',{})
    episode.call('simulate_plan',dict(actions=[]));episode.call('simulate_plan',dict(actions=[]))
    with pytest.raises(ValueError,match='budget'):episode.call('simulate_plan',dict(actions=[]))
    for name,args in [('observe',{'private':True}),('python',{}),('act',{'kind':'hold','reward':1})]:
        with pytest.raises(ValueError):episode.call(name,args)
    episode.finish()
    with pytest.raises(ValueError):episode.call('observe',{})


def test_live_tool_runner_uses_actual_public_feedback(task):
    plan=teacher_episode(task,'heuristic')['plan'];requests=iter([dict(name='act',arguments=a) for a in plan['actions']]+[dict(name='finish',arguments={})])
    seen=[]
    def generate(messages):
        seen.append(copy.deepcopy(messages))
        assert task.scenario.hidden_canary not in encoded(messages).decode()
        messages.clear() # cannot mutate runner history
        return encoded(next(requests)).decode()
    result=rollout_tools(task,generate)
    assert result['score']==score_plan(task,encoded(plan).decode())
    assert len(seen[-1])==2+2*len(plan['actions'])
    assert 'tool_result' in seen[-1][-1]['content']


@pytest.mark.parametrize('tool_request',[dict(name='finish',arguments={'reward':1}),dict(name='act',arguments={'kind':'hold','reward':1}),dict(name='read_private',arguments={})])
def test_malformed_tools_fail_closed(task,tool_request):
    assert rollout_tools(task,lambda _:encoded(tool_request).decode())['score']['reward']==-1


def test_group_reset_order_and_zero_variance(task):
    good=encoded(teacher_episode(task,'heuristic')['plan']).decode();bad='garbage'
    group=score_group(task,[good,bad,good])
    assert group['informative'] and group['rewards'][0]==group['rewards'][2]
    assert sum(group['advantages'])==pytest.approx(0,abs=1e-12)
    assert score_group(task,[bad,good])['rewards']==list(reversed(score_group(task,[good,bad])['rewards']))
    zero=score_group(task,[good,good]);assert not zero['informative'] and zero['advantages']==[0,0]


def test_solver_failures_discard_group(task,monkeypatch):
    import power_grid_env.llm_training as module
    monkeypatch.setattr(module,'score_plan',lambda *a:dict(reward=None,status='simulation_failure'))
    result=score_group(task,['{}','{}']);assert result['rewards'] is None and result['advantages'] is None


def test_corpus_export_only_public_and_transferable(corpus,tmp_path):
    checked=verify_corpus(corpus);assert checked['tasks']==12
    groups={split:{r['topology_group'] for r in _read_lines(corpus/'public'/split/'prompts.jsonl')} for split in ['train','dev','test']}
    assert not groups['train']&groups['test'] and not groups['dev']&groups['test'] and not groups['train']&groups['dev']
    out=tmp_path/'export';r=export_public_dataset(corpus,out,split='train',kind='plans')
    assert r['rows']==6 and not r['private_files_exported']
    raw=(out/'data.jsonl').read_text()
    for forbidden in ['PRIVATE_ONLY_','privileged','fault_r_ohm','candidate_scores']:assert forbidden not in raw
    assert {p.name for p in out.iterdir()}=={'data.jsonl','dataset.json','manifest.json'}
    export_zip(out,tmp_path/'data.zip');extract_zip(tmp_path/'data.zip',tmp_path/'restored')
    assert verify(out)==verify(tmp_path/'restored')


@pytest.mark.parametrize('defect',['observation','trace','candidate','prompt','task'])
def test_resealed_fabrications_are_semantically_rejected(corpus,tmp_path,defect):
    root=tmp_path/'tampered';shutil.copytree(corpus,root);(root/'manifest.json').unlink()
    task=read_json(root/'corpus.json')['tasks'][0]['task_id'];folder=root/'private/episodes'/task
    if defect=='observation':
        path=folder/'teacher.json';data=read_json(path);data['public_steps'][0]['response']['accepted']=False
    elif defect=='trace':
        path=folder/'physical-trace.json';data=read_json(path);data[0]['origin']='forged'
    elif defect=='candidate':
        path=folder/'teacher.json';data=read_json(path);data['candidate_scores']['hold']['reward']=100
    elif defect=='task':
        path=root/'private/tasks'/(task+'.json');data=read_json(path);data['scenario']['hidden_canary']='forged'
    else:
        path=root/'public/train/plans.jsonl';rows=_read_lines(path);rows[0]['prompt'][0]['content']='forged'
        path.write_bytes(b''.join(encoded(row)+b'\n' for row in rows));seal(root)
        with pytest.raises(ValueError):verify_corpus(root)
        return
    path.write_bytes(encoded(data));seal(root)
    with pytest.raises(ValueError):verify_corpus(root)


def test_trl_callback_returns_raw_rewards_and_enforces_split(corpus):
    verifier=CorpusVerifier(corpus);rows=_read_lines(corpus/'public/train/plans.jsonl');row=rows[0]
    text=row['completion'][0]['content'];identifier=row['task_id']
    raw=verifier.trl_reward([row['completion'],[None],'bad'],[identifier]*3)
    assert raw==[verifier.score(identifier,text)['reward'],-1,-1]
    assert raw[0]>0
    heldout=_read_lines(corpus/'public/test/plans.jsonl')[0]
    with pytest.raises(ValueError,match='forbidden'):verifier.score(heldout['task_id'],text)
    with pytest.raises(ValueError):verifier.trl_reward([text],[])


def test_sft_step_and_tool_schema_match_outputs(corpus):
    rows=_read_lines(corpus/'public/train/steps.jsonl');toolrows=_read_lines(corpus/'public/train/tools.jsonl')
    from jsonschema import validate
    for row in rows+toolrows:
        payload=json.loads(row['prompt'][1]['content']);validate(json.loads(row['completion'][0]['content']),payload['response_schema'])


def test_optional_training_contract_and_no_truncation(corpus):
    with pytest.raises(ValueError):TrainingRecipe.model_validate(dict(mode='grpo',corpus='x',checkpoint='x',output='x',num_generations=1))
    class Tokenizer:
        def apply_chat_template(self,*a,**kw):return [0]*200
    with pytest.raises(ValueError,match='truncation'):checked_rows(corpus,'plans',Tokenizer(),128)
    with pytest.raises(ValueError,match='completion'):checked_rows(corpus,'prompts',Tokenizer(),256,completion_budget=100)
    rows=checked_rows(corpus,'prompts',Tokenizer(),512,completion_budget=100)
    assert set(rows[0])=={'prompt','task_id'}


def test_checkpoint_hash_includes_weights_and_tokenizer(tmp_path):
    from power_grid_env.llm_fit import checkpoint_identity,train_local
    from power_grid_env.llm_inference import InferenceRecipe,evaluate_local
    model=tmp_path/'checkpoint';model.mkdir();(model/'config.json').write_text('{}')
    (model/'model.safetensors').write_bytes(b'weights')
    first=checkpoint_identity(model);(model/'model.safetensors').write_bytes(b'changed')
    assert first['sha256']!=checkpoint_identity(model)['sha256']
    recipe=dict(corpus='x',checkpoint=str(model),output=str(model/'nested'),use_cpu=True)
    with pytest.raises(ValueError,match='outside'):train_local(dict(recipe,mode='sft'))
    with pytest.raises(ValueError,match='outside'):evaluate_local(recipe)
    with pytest.raises(ValueError):InferenceRecipe.model_validate(dict(recipe,split='train'))


def test_what_if_does_not_read_hidden_fault_resistance(task):
    changed=task.model_copy(deep=True);changed.scenario.fault_r_ohm=5.
    first=LLMEpisode(task);second=LLMEpisode(changed)
    assert first.call('simulate_plan',dict(actions=[]))==second.call('simulate_plan',dict(actions=[]))


def test_tool_call_budget_cannot_be_exceeded(task):
    result=rollout_tools(task,lambda _: '{"name":"observe","arguments":{}}')
    assert result['score']['reason']=='tool_call_budget_exceeded'
    assert len(result['messages'])==50


def test_trl_solver_failure_is_not_a_negative_training_label(corpus,monkeypatch):
    verifier=CorpusVerifier(corpus);identifier=next(iter(verifier._tasks))
    monkeypatch.setattr(verifier,'score',lambda *a:dict(reward=None))
    with pytest.raises(RuntimeError,match='solver failure'):verifier.trl_reward(['{}'],[identifier])


def test_heldout_evaluation_requires_complete_split(corpus):
    from power_grid_env.llm_training import evaluate_predictions
    rows=_read_lines(corpus/'public/test/plans.jsonl')
    predictions=[dict(task_id=r['task_id'],completion=r['completion'][0]['content']) for r in rows]
    result=evaluate_predictions(corpus,predictions)
    assert result['status']=='ok' and result['tasks']==3 and result['feasible_fraction']==1
    for bad in [predictions[:-1],predictions+[predictions[0]],predictions+[dict(task_id='unknown',completion='{}')]]:
        with pytest.raises(ValueError):evaluate_predictions(corpus,bad)
    with pytest.raises(ValueError):evaluate_predictions(corpus,predictions,split='train')


def test_cli_verify_and_no_overwrite(corpus,tmp_path,capsys):
    from power_grid_env.cli import main
    assert main(['llm-data','verify',str(corpus)])==0
    out=tmp_path/'export'
    args=['llm-data','export',str(corpus),'--out',str(out)]
    assert main(args)==0 and main(args)==2
    assert 'new public export' in capsys.readouterr().out

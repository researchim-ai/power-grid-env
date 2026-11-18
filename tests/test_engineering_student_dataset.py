import json
import shutil
from pathlib import Path
import pytest
from benchmarks.engineering_factory import build,write_json,ProceduralDataset
from benchmarks.engineering_learning_gate import create_suite,strict_contract,evaluate_cases
from benchmarks.engineering_student_recovery import export
from benchmarks.engineering_student_dataset import StudentCorrectionDataset
from power_grid_env.study_artifacts import seal,verify,read_json


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root=tmp_path_factory.mktemp('student-dataset');build(root/'data',420,1,(8,1,1))
    tm=verify(root/'data'/'train');s=create_suite(root/'data'/'train',tm,root/'suite');sm=verify(root/'suite')
    data=ProceduralDataset(root/'data'/'train',tm,strict_contract());cases=[data.scenarios[t] for t in s['task_ids']]
    # Synthetic policy is only a test fixture;never evidence of native model training.
    def policy(messages):
        if json.loads(messages[1]['content'])['task']['id']==cases[0]['id']:raise RuntimeError('fixture transport failure')
        return json.dumps(dict(name='inspect_project',arguments={}))
    evaluate_cases(cases,policy,root/'student',strict_contract(),dict(role='tiny_train',corpus_manifest_sha256=tm,suite_manifest_sha256=sm),dict(kind='native_model',fixture_only=True))
    em=verify(root/'student');export(root/'data'/'train',tm,root/'suite',sm,root/'student',em,root/'corpus')
    return root,tm,sm,em


def args(corpus):
    r,t,s,e=corpus
    return (r/'corpus',verify(r/'corpus'),r/'data'/'train',t,r/'suite',s,r/'student',e)


def test_every_positive_label_replays_and_student_actions_are_only_context(corpus):
    data=StudentCorrectionDataset(*args(corpus))
    assert len(data)>0 and data.summary['source_turns']==15 and data.summary['visited_prefixes']==14
    assert len(data.episodes)==14
    for index in range(len(data)):
        messages,action,episode,turn=data.example(index)
        assert episode['events'][turn]['source']=='public_expert' and action==episode['events'][turn]['action']
        assert messages[2]['content']==episode['events'][0]['completion']
        assert all(e['source']=='student' for e in episode['events'][:episode['prefix_calls']])
    with pytest.raises(ValueError):data.example(True)
    with pytest.raises(ValueError):data.example(len(data))
    with pytest.raises(ValueError):StudentCorrectionDataset(*args(corpus),contract=strict_contract().model_copy(update={'context_tokens':16384}))


@pytest.mark.parametrize('change',['target','student_source','failed_attempt','missing_file','teacher','candidate','evaluation_anchor'])
def test_resealed_forgery_or_discarded_failure_never_enters_training(corpus,tmp_path,change):
    r,t,s,e=corpus;path=tmp_path/'bad';shutil.copytree(r/'corpus',path,ignore=shutil.ignore_patterns('manifest.json'))
    summary=read_json(path/'summary.json')
    if change=='target':
        p=path/'episodes.jsonl';rows=[json.loads(x) for x in p.read_text().splitlines()];rows[0]['events'][-1]['action']['name']='validate';p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    elif change=='student_source':
        p=next((path/'attempts').glob('*.json'));ep=read_json(p);ep['events'][0]['source']='public_expert';write_json(p,ep)
    elif change=='failed_attempt':summary['attempts']=[a for a in summary['attempts'] if a['status']!='transport_failure_no_action'];write_json(path/'summary.json',summary)
    elif change=='missing_file':next((path/'attempts').glob('*.json')).unlink()
    elif change=='teacher':summary['teacher_sha256']='0'*64;write_json(path/'summary.json',summary)
    elif change=='candidate':summary['source_candidate']['kind']='public_deterministic_control';write_json(path/'summary.json',summary)
    else:summary['student_evaluation_manifest_sha256']='0'*64;write_json(path/'summary.json',summary)
    with pytest.raises(ValueError):StudentCorrectionDataset(path,seal(path),r/'data'/'train',t,r/'suite',s,r/'student',e)


def test_batch_uses_native_packer_with_only_current_expert_target(corpus,monkeypatch):
    import benchmarks.engineering_student_dataset as module
    data=StudentCorrectionDataset(*args(corpus));seen=[]
    def pack(processor,messages,action,contract):seen.append((messages,action));return {'labels':'sentinel'},dict(prompt_tokens=42,label_tokens=9,total_tokens=51)
    monkeypatch.setattr(module,'pack_action',pack)
    batch,meta=data.batch(None,0)
    assert batch['labels']=='sentinel' and meta['source']=='public_expert' and meta['context_source']=='actual_student_rollout'
    assert seen[0][1]==data.example(0)[1]

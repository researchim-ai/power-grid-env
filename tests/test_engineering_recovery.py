import copy
import json
import pytest
from benchmarks import engineering_agent as env
from benchmarks.engineering_recovery import VARIANTS,collect,replay,episode_messages,RecoveryPolicy

TRAIN=[t for t in env.registry() if t['split']=='train']


@pytest.mark.parametrize('task',TRAIN,ids=lambda t:t['id'])
@pytest.mark.parametrize('variant',VARIANTS)
def test_recovery_positive_physics_and_source_masks(task,variant):
    episode=collect(task['id'],variant)
    assert episode['success']
    assert episode['events'][-1]['action']['name']=='submit'
    assert episode['events'][-1]['source']=='public_expert'
    assert episode['events'][-1]['response']['result']['reward']==1
    assert sum(e['source']=='scripted_perturbation' for e in episode['events'])==int(variant!='clean')
    for messages,event in episode_messages(episode):
        if event['source']=='public_expert':assert RecoveryPolicy()(messages)==event['action']


def test_heldout_cannot_enter_recovery_training():
    for t in env.registry():
        if t['split']!='train':
            with pytest.raises(ValueError):collect(t['id'],'simulate_first')


def test_tampered_transitions_and_sources_rejected():
    episode=collect(TRAIN[0]['id'],'invalid_arguments');replay(episode)
    bad=copy.deepcopy(episode);bad['events'][0]['source']='public_expert'
    with pytest.raises(ValueError):replay(bad)
    bad=copy.deepcopy(episode);bad['events'][1]['response']['ok']=False
    with pytest.raises(ValueError):replay(bad)


def test_new_error_after_inspection_forces_reinspection():
    e=collect(TRAIN[0]['id'],'clean')
    messages,_=list(episode_messages(e))[1]
    messages.extend([dict(role='assistant',content='{"name":"inspect_equipment","arguments":{"equipment_id":"unknown"}}'),
                     dict(role='user',content='{"ok":false,"error":"unknown"}')])
    assert RecoveryPolicy()(messages)==dict(name='inspect_project',arguments={})


def test_reusable_dataset_supervises_only_expert_continuations(tmp_path):
    import torch
    from benchmarks.engineering_recovery import export,RecoveryDataset
    from benchmarks.engineering_protocol import RunContract
    from power_grid_env.study_artifacts import verify
    root=tmp_path/'data';export(root)
    class Processor:
        def apply_chat_template(self,*args,**kwargs):
            n=10 if kwargs['add_generation_prompt'] else 20
            ids=torch.arange(n).reshape(1,n)
            return dict(input_ids=ids,attention_mask=torch.ones_like(ids))
    for protocol in ('json-action-v1','qwen-native-v1'):
        data=RecoveryDataset(root,verify(root),RunContract(protocol=protocol))
        assert len(data)==189 and len(data.episodes)==42
        assert all(data.episodes[i]['events'][turn]['source']=='public_expert' for i,turn in data.targets)
        index=next(i for i,(episode,turn) in enumerate(data.targets) if data.episodes[episode]['variant']=='invalid_arguments')
        batch,meta=data.batch(Processor(),index)
        assert meta['turn']==1 and meta['source']=='public_expert'
        assert (batch['labels'][0,:10]==-100).all() and (batch['labels'][0,10:]==batch['input_ids'][0,10:]).all()
        with pytest.raises(ValueError):data.batch(Processor(),True)


def test_resealed_corpus_cannot_relabel_injected_errors(tmp_path):
    from benchmarks.engineering_recovery import export,RecoveryDataset
    from benchmarks.engineering_protocol import RunContract
    from power_grid_env.study_artifacts import seal
    import shutil
    root=tmp_path/'original';export(root)
    tampered=tmp_path/'tampered';shutil.copytree(root,tampered,ignore=shutil.ignore_patterns('manifest.json'))
    p=tampered/'episodes.jsonl';rows=[json.loads(x) for x in p.read_text().splitlines()]
    rows[1]['events'][0]['source']='public_expert'
    p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    with pytest.raises(ValueError):RecoveryDataset(tampered,seal(tampered),RunContract())

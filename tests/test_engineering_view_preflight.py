import json
import pytest
from benchmarks.engineering_view_preflight import preflight
from power_grid_env.study_artifacts import seal


class Processor:
    def apply_chat_template(self,*args,**kwargs):
        class Tensor:
            shape=(1,10)
        return dict(input_ids=Tensor())


def fixture(root,split='train'):
    root.mkdir()
    (root/'summary.json').write_text(json.dumps(dict(split=split,model_target_sha256='a'*64,examples=1)))
    (root/'train-actions.jsonl').write_text(json.dumps(dict(task_id='task-a',split=split,messages=[dict(role='user',content='{}')]))+'\n')
    return seal(root)


def test_owner_pinned_train_preflight(tmp_path):
    root=tmp_path/'corpus';manifest=fixture(root)
    result=preflight(Processor(),root,manifest,'a'*64,tmp_path/'out')
    assert result['examples']==1 and result['raw_tokens']==10 and result['compact_tokens']==10
    assert not result['weights_loaded']
    with pytest.raises(ValueError):preflight(Processor(),root,'b'*64,'a'*64,tmp_path/'bad')
    with pytest.raises(ValueError):preflight(Processor(),root,manifest,'a'*64,tmp_path/'out')


def test_dev_not_accepted_as_training_preflight(tmp_path):
    root=tmp_path/'dev';manifest=fixture(root,'dev')
    with pytest.raises(ValueError):preflight(Processor(),root,manifest,'a'*64,tmp_path/'out')

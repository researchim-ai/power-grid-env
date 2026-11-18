import json
import pytest
from benchmarks.engineering_supervision import export


class Tensor:
    def __init__(self,ids):self.ids=ids;self.shape=(1,len(ids))
    def __getitem__(self,i):return self
    def tolist(self):return self.ids


class Processor:
    mismatch=False
    def apply_chat_template(self,messages,**kwargs):
        assert kwargs['enable_thinking'] is False
        ids=list(range(10 if kwargs['add_generation_prompt'] else 20))
        if self.mismatch and not kwargs['add_generation_prompt']:ids[0]=999
        return dict(input_ids=Tensor(ids))


def test_train_only_exact_prefix_labels(tmp_path):
    summary=export(Processor(),tmp_path/'corpus')
    rows=[json.loads(l) for l in (tmp_path/'corpus'/'train-actions.jsonl').read_text().splitlines()]
    assert summary['examples']==38 and len(rows)==38
    assert {r['split'] for r in rows}=={'train'}
    assert len({r['task_id'] for r in rows})==7
    assert all(r['label_tokens']==10 and not r['eligible_for_gold_sft'] for r in rows)
    assert all(r['completion']['role']=='assistant' for r in rows)
    assert all('private_state' not in json.dumps(r['messages']) for r in rows)


def test_token_prefix_mismatch_is_rejected(tmp_path):
    processor=Processor();processor.mismatch=True
    with pytest.raises(ValueError,match='qualification failed'):export(processor,tmp_path/'corpus')
    assert not (tmp_path/'corpus'/'summary.json').exists()


def test_full_label_context_overflow_rejected(tmp_path):
    with pytest.raises(ValueError,match='qualification failed'):export(Processor(),tmp_path/'corpus',context_tokens=15)
    assert not (tmp_path/'corpus'/'summary.json').exists()

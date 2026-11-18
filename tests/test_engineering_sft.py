import json
from pathlib import Path
import pytest
import torch
from benchmarks.engineering_supervision import export
from benchmarks.engineering_sft import ActionDataset
from power_grid_env.study_artifacts import seal, verify
from power_grid_env.qwen35_training import supervised_forward

TARGET='a'*64


class Processor:
    def apply_chat_template(self,messages,**kwargs):
        n=10 if kwargs['add_generation_prompt'] else 20
        return dict(input_ids=torch.arange(n).reshape(1,n),attention_mask=torch.ones(1,n,dtype=torch.long))


@pytest.fixture
def corpus(tmp_path):
    import shutil
    original=tmp_path/'exported';export(Processor(),original,target_sha256=TARGET)
    root=tmp_path/'candidate'
    shutil.copytree(original,root,ignore=shutil.ignore_patterns('manifest.json'))
    return root


def load(root):return ActionDataset(root,verify(root) if (root/'manifest.json').exists() else seal(root),TARGET)


def test_batch_masks_prompt_and_allows_assistant_eos(corpus):
    dataset=load(corpus)
    batch,meta=dataset.batch(Processor(),0)
    assert len(dataset)==38 and meta['label_tokens']==10
    assert torch.equal(batch['labels'][0,:10],torch.full((10,),-100))
    assert torch.equal(batch['labels'][0,10:],batch['input_ids'][0,10:])
    assert batch['labels'][0,-1] != -100
    with pytest.raises(ValueError):dataset.batch(Processor(),True)
    with pytest.raises(ValueError):dataset.batch(Processor(),38)


def edit(root,mutate):
    p=root/'train-actions.jsonl';rows=[json.loads(l) for l in p.read_text().splitlines()]
    mutate(rows);p.write_text(''.join(json.dumps(r)+'\n' for r in rows))


@pytest.mark.parametrize('mutation',[
 lambda rows:rows[0].update(split='dev'),
 lambda rows:rows[0].update(family_sha256='wrong'),
 lambda rows:rows[0].update(eligible_for_gold_sft=True),
 lambda rows:rows[0].update(prompt_tokens=True),
 lambda rows:rows[0]['loss_mask'].update(prompt_tokens=1),
 lambda rows:rows[1]['messages'][-1].update(content='forged solver output'),
 lambda rows:rows.pop(),
 lambda rows:rows[0]['completion'].update(content='{"name":"export","arguments":{}}'),
])
def test_resealed_bad_records_rejected(corpus,mutation):
    edit(corpus,mutation)
    with pytest.raises(ValueError):load(corpus)


def test_manifest_and_target_are_owner_anchors(corpus):
    manifest=seal(corpus)
    with pytest.raises(ValueError):ActionDataset(corpus,'0'*64,TARGET)
    with pytest.raises(ValueError):ActionDataset(corpus,manifest,'b'*64)
    edit(corpus,lambda rows:rows[0].update(total_tokens=21))
    with pytest.raises(ValueError):ActionDataset(corpus,manifest,TARGET)


def test_retokenization_rejects_counts_or_prefix(corpus):
    dataset=load(corpus)
    class Bad(Processor):
        def apply_chat_template(self,messages,**kwargs):
            batch=super().apply_chat_template(messages,**kwargs)
            if not kwargs['add_generation_prompt']:batch['input_ids'][0,0]=999
            return batch
    with pytest.raises(ValueError):dataset.batch(Bad(),0)


def test_selected_loss_matches_full_causal_reference(corpus):
    dataset=load(corpus);batch,_=dataset.batch(Processor(),0)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__();self.full=torch.nn.Parameter(torch.randn(1,20,24))
        def forward(self,input_ids,attention_mask,use_cache,logits_to_keep):
            from types import SimpleNamespace
            assert use_cache is False
            return SimpleNamespace(logits=self.full[:,logits_to_keep,:])
    torch.manual_seed(8);model=Model()
    loss,_=supervised_forward(model,batch)
    reference=torch.nn.functional.cross_entropy(model.full[:,:-1,:].reshape(-1,24),batch['labels'][:,1:].reshape(-1),ignore_index=-100)
    assert torch.allclose(loss,reference)
    loss.backward();gradient=model.full.grad
    assert torch.count_nonzero(gradient[:,:9,:])==0
    assert torch.count_nonzero(gradient[:,9:19,:])>0
    assert torch.count_nonzero(gradient[:,19:,:])==0

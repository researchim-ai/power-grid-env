import torch
import pytest
from benchmarks.engineering_train import schedule,save_optimizer,restore_optimizer


def step(model,optimizer,x):
    optimizer.zero_grad(set_to_none=True)
    loss=(model(x)-.3).square().sum();loss.backward();optimizer.step()


def test_schedule_complete_deterministic_and_epoch_permutations():
    a=schedule(38,3,42)
    assert a==schedule(38,3,42) and a!=schedule(38,3,43)
    assert all(sorted(a[i:i+38])==list(range(38)) for i in range(0,len(a),38))
    for size,epochs in [(0,1),(2,0),(2,9),(True,1)]:
        with pytest.raises(ValueError):schedule(size,epochs,42)


def test_resumed_adamw_matches_uninterrupted_exactly(tmp_path):
    torch.manual_seed(10)
    a=torch.nn.Linear(3,2);b=torch.nn.Linear(3,2);b.load_state_dict(a.state_dict())
    oa=torch.optim.AdamW(a.parameters(),lr=.0002,weight_decay=0.)
    ob=torch.optim.AdamW(b.parameters(),lr=.0002,weight_decay=0.)
    inputs=[torch.randn(1,3) for _ in range(4)]
    for x in inputs:step(a,oa,x)
    for x in inputs[:2]:step(b,ob,x)
    names=save_optimizer(b,ob,tmp_path/'optimizer.safetensors')
    saved_rng=torch.get_rng_state().clone()
    c=torch.nn.Linear(3,2);c.load_state_dict(b.state_dict())
    oc=torch.optim.AdamW(c.parameters(),lr=.0002,weight_decay=0.)
    restore_optimizer(c,oc,tmp_path/'optimizer.safetensors',names,2)
    assert torch.equal(saved_rng,torch.get_rng_state())
    for x in inputs[2:]:step(c,oc,x)
    assert all(torch.equal(p,q) for p,q in zip(a.parameters(),c.parameters()))
    for p,q in zip(a.parameters(),c.parameters()):
        assert all(torch.equal(oa.state[p][key],oc.state[q][key]) for key in oa.state[p])


def test_incompatible_optimizer_checkpoint_rejected(tmp_path):
    model=torch.nn.Linear(2,1);opt=torch.optim.AdamW(model.parameters())
    with pytest.raises(ValueError):save_optimizer(model,opt,tmp_path/'empty.safetensors')
    step(model,opt,torch.ones(1,2))
    names=save_optimizer(model,opt,tmp_path/'state.safetensors')
    for n,cursor in [(names[::-1],1),(names,2)]:
        with pytest.raises(ValueError):restore_optimizer(model,opt,tmp_path/'state.safetensors',n,cursor)
    from safetensors.torch import load_file,save_file
    data=load_file(str(tmp_path/'state.safetensors'))
    data[names[0]+'/exp_avg'][0]=float('nan')
    save_file(data,str(tmp_path/'corrupt.safetensors'))
    with pytest.raises(ValueError):restore_optimizer(model,opt,tmp_path/'corrupt.safetensors',names,1)


def test_trainer_full_split_resume_pipeline_exact(tmp_path,monkeypatch):
    import benchmarks.engineering_sft as data_module
    import power_grid_env.qwen35_training as native
    from benchmarks.engineering_train import run
    from power_grid_env.study_artifacts import verify
    from safetensors.torch import load_file
    class Dataset:
        def __init__(self,*args):pass
        def __len__(self):return 3
        def batch(self,processor,index):
            ids=torch.tensor([[0,1,2,index+3]])
            return dict(input_ids=ids,attention_mask=torch.ones_like(ids),labels=torch.tensor([[-100,-100,2,index+3]])),dict(index=index)
    class Model(torch.nn.Module):
        def __init__(self):super().__init__();self.full=torch.nn.Parameter(torch.randn(1,4,8))
        def forward(self,input_ids,attention_mask,use_cache,logits_to_keep):
            from types import SimpleNamespace
            return SimpleNamespace(logits=self.full[:,logits_to_keep,:])
    monkeypatch.setattr(data_module,'ActionDataset',Dataset)
    monkeypatch.setattr(native,'local_target',lambda *args:{})
    monkeypatch.setattr(native,'load_native',lambda *args:(None,Model()))
    monkeypatch.setattr(native,'attach_adapters',lambda *args:['full'])
    args=('target','a'*64,'corpus','b'*64)
    full=run(*args,tmp_path/'full')
    head=run(*args,tmp_path/'head',max_steps=1)
    assert not head['schedule_complete']
    resumed=run(*args,tmp_path/'tail',resume=tmp_path/'head',resume_manifest=verify(tmp_path/'head'))
    import json
    a_receipt=json.loads((tmp_path/'full'/'summary.json').read_text())
    b_receipt=json.loads((tmp_path/'tail'/'summary.json').read_text())
    assert a_receipt['history']==b_receipt['history'] and resumed['schedule_complete']
    for filename in ('adapter.safetensors','optimizer.safetensors'):
        a=load_file(str(tmp_path/'full'/filename));b=load_file(str(tmp_path/'tail'/filename))
        assert a.keys()==b.keys() and all(torch.equal(a[k],b[k]) for k in a)
    with pytest.raises(ValueError):run(*args,tmp_path/'bad',epochs=2,resume=tmp_path/'head',resume_manifest=verify(tmp_path/'head'))
    with pytest.raises(ValueError):run(*args,tmp_path/'done',resume=tmp_path/'full',resume_manifest=verify(tmp_path/'full'))

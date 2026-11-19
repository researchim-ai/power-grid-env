import pytest


@pytest.fixture
def example():
    torch=pytest.importorskip('torch')
    from torch import nn
    class Layer(nn.Module):
        def __init__(self):super().__init__();self.linear=nn.Linear(4,4)
        def forward(self,hidden_states,**kwargs):return hidden_states+torch.tanh(self.linear(hidden_states))
    class Qwen3_5ForConditionalGeneration(nn.Module):
        def __init__(self):
            super().__init__()
            self.model=nn.Module();self.model.language_model=nn.Module()
            text=self.model.language_model
            text.embed_tokens=nn.Embedding(20,4)
            text.layers=nn.ModuleList([Layer() for _ in range(3)])
            text.norm=nn.LayerNorm(4);self.lm_head=nn.Linear(4,20,bias=False)
            self.requires_grad_(False)
            for layer in text.layers[1:]:layer.requires_grad_(True)
        def forward(self,input_ids,attention_mask,use_cache,logits_to_keep):
            text=self.model.language_model;hidden=text.embed_tokens(input_ids)
            for layer in text.layers:
                hidden=layer(hidden,position_embeddings=(torch.zeros_like(hidden),torch.ones_like(hidden)),
                             attention_mask=attention_mask,past_key_values=None,use_cache=use_cache)
            hidden=text.norm(hidden)
            from types import SimpleNamespace
            return SimpleNamespace(logits=self.lm_head(hidden[:,logits_to_keep,:]))
    torch.manual_seed(1);model=Qwen3_5ForConditionalGeneration().eval()
    batch=dict(input_ids=torch.tensor([[1,2,3,4]]),attention_mask=torch.ones((1,4),dtype=torch.long),
               labels=torch.tensor([[-100,-100,3,4]]))
    return model,batch


def test_cache_matches_full_model_before_and_after_tail_update(example):
    import torch
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    model,batch=example;cache=FrozenPrefixCache(model)
    assert cache.prepare('a',batch)['bit_exact']
    with torch.no_grad():model.model.language_model.layers[-1].linear.weight.add_(.02)
    assert cache.verify('a',batch)['bit_exact']
    assert all(not layer._forward_pre_hooks for layer in model.model.language_model.layers)


def test_cache_rejects_grad_mode_and_changed_input(example):
    import torch
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    model,batch=example;cache=FrozenPrefixCache(model);cache.prepare('a',batch)
    with pytest.raises(ValueError,match='inference-only'):cache.forward('a',batch)
    changed={k:v.clone() for k,v in batch.items()};changed['input_ids'][0,0]=9
    with torch.inference_mode(),pytest.raises(ValueError,match='input changed'):
        cache.forward('a',changed)


def test_cache_rejects_trainable_prefix_or_head(example):
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    model,_=example;model.lm_head.requires_grad_(True)
    with pytest.raises(ValueError,match='trainable parameter affects'):
        FrozenPrefixCache(model)


def test_cache_requires_fresh_eval_entries(example):
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    model,batch=example;cache=FrozenPrefixCache(model);cache.prepare('a',batch)
    with pytest.raises(ValueError,match='fresh eval-mode'):cache.prepare('a',batch)
    model.train()
    with pytest.raises(ValueError,match='fresh eval-mode'):cache.prepare('b',batch)


def test_capture_hooks_are_removed_when_native_forward_fails(example,monkeypatch):
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    model,batch=example;cache=FrozenPrefixCache(model)
    def fail(*args,**kwargs):raise RuntimeError('native layer failure')
    monkeypatch.setattr(model.model.language_model.layers[-1],'forward',fail)
    with pytest.raises(RuntimeError,match='native layer failure'):
        cache.prepare('broken',batch)
    assert not cache.entries
    assert all(not layer._forward_pre_hooks for layer in model.model.language_model.layers)

from types import SimpleNamespace
import pytest
import torch
from benchmarks.engineering_protocol import native_completion
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_target_semantics import verify_target


def batch():return dict(input_ids=torch.tensor([[1,2,3,4]]),labels=torch.tensor([[-100,-100,3,4]]))
def processor(raw):return SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda *a,**k:raw,encode=lambda value,**k:list(value)))


def test_actual_decoded_target_preserves_required_arguments_exactly():
    action=dict(name='add_line',arguments=dict(route_id='route_123',equipment_id='line_456',catalog_id='copper-1'))
    meta=verify_target(processor(native_completion(action)),batch(),action,strict_contract())
    assert meta['argument_fields']==3 and meta['argument_value_tokens']>0


@pytest.mark.parametrize('wrong',[
 dict(name='add_line',arguments={}),
 dict(name='add_line',arguments=dict(route_id='wrong')),
 dict(name='add_load',arguments=dict(load_id='load1')),
])
def test_dropped_or_corrupted_arguments_never_pass(wrong):
    expected=dict(name='add_line',arguments=dict(route_id='route_123',equipment_id='line_456',catalog_id='copper-1'))
    with pytest.raises(ValueError):verify_target(processor(native_completion(wrong)),batch(),expected,strict_contract())


@pytest.mark.parametrize('labels',[[0,-100,3,4],[-100,2,-100,4],[-100,-100,4,4],[-100,-100,-100,-100]])
def test_target_must_be_one_exact_nonempty_causal_suffix(labels):
    b=batch();b['labels']=torch.tensor([labels]);action=dict(name='simulate',arguments={})
    with pytest.raises(ValueError):verify_target(processor(native_completion(action)),b,action,strict_contract())

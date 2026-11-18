import json
import pytest
from benchmarks.engineering_value_loss import value_spans,payload_mask


class Characters:
    def decode(self,ids,skip_special_tokens=False):return ''.join(chr(i) for i in ids)
    def __call__(self,text,**kw):return dict(input_ids=[ord(c) for c in text],offset_mapping=[(i,i+1) for i in range(len(text))])


@pytest.mark.parametrize('answer',[
    {'operation':'set_line_catalog'}, {'value':None}, {'ready':False},
    {'route_id':'R1','catalog_id':'copper-2'}, {'value':1.25}, {'escaped':'a"\\b'}, {'unicode':'ток'},
])
def test_value_spans_cover_only_primitive_contents_not_keys_or_json_syntax(answer):
    text,spans=value_spans(answer);ids=[ord(c) for c in text+'\n']
    mask=payload_mask(Characters(),ids,answer)
    assert len(mask)==len(ids) and any(mask) and mask[-1] is False
    assert all(mask[i]==any(a<=i<b for a,b in spans) for i in range(len(text)))
    assert not mask[0] and not mask[1]
    for (key,value),(a,b) in zip(answer.items(),spans):
        literal=json.dumps(value,separators=(',',':'))
        assert text[a:b]==(literal[1:-1] if isinstance(value,str) else literal)


@pytest.mark.parametrize('answer',[{},[],{'x':[]},{'x':{}},{'x':('a','b')},{'x':''},{'x':float('inf')}, {1:'a'}])
def test_unsupported_or_empty_payloads_fail_instead_of_silent_uniform_loss(answer):
    with pytest.raises(ValueError):value_spans(answer)


def test_tokenization_drift_and_answer_drift_are_rejected():
    class Drift(Characters):
        def __call__(self,text,**kw):return dict(input_ids=[1],offset_mapping=[(0,len(text))])
    ids=list(map(ord,'{"value":null}\n'))
    with pytest.raises(ValueError):payload_mask(Drift(),ids,{'value':None})
    with pytest.raises(ValueError):payload_mask(Characters(),ids,{'value':'10'})


def test_payload_weight_has_expected_gradient_ratio_without_prompt_or_image_tokens():
    import torch
    from benchmarks.engineering_value_loss import objective
    logits=torch.zeros((1,2,2),requires_grad=True);targets=torch.tensor([0,0])
    loss,m=objective(logits,targets,[False,True],4.)
    loss.backward()
    assert float(loss.detach())==pytest.approx(0.69314718)
    assert logits.grad[0,0].tolist()==pytest.approx([-.1,.1])
    assert logits.grad[0,1].tolist()==pytest.approx([-.4,.4])
    assert m['assistant_tokens']==2 and m['payload_tokens']==1


def test_uniform_payload_weight_matches_native_selected_causal_mean_ce():
    import torch
    from benchmarks.engineering_value_loss import objective
    logits=torch.tensor([[[1.,0.],[1.,0.]]]);targets=torch.tensor([0,1])
    loss,m=objective(logits,targets,[False,True],1.)
    expected=torch.nn.functional.cross_entropy(logits[0],targets)
    assert torch.equal(loss,expected)
    assert m['payload_token_accuracy']==0 and m['payload_ce']>m['syntax_ce']


@pytest.mark.parametrize('weight',[float('nan'),float('inf'),0,17,True])
def test_invalid_payload_weight_is_rejected(weight):
    import torch
    from benchmarks.engineering_value_loss import objective
    with pytest.raises(ValueError):objective(torch.zeros((1,2,2)),torch.tensor([0,0]),[False,True],weight)

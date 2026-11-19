import copy
import pytest

from benchmarks.engineering_balanced_training import payload_kl,metrics,validate_result,probe_entries


def test_payload_distillation_zero_at_parent_and_positive_after_drift():
    torch=pytest.importorskip('torch')
    reference=torch.tensor([[2.,0.,-1.],[0.,1.,3.]])
    teacher=reference.log_softmax(-1)
    student=reference.clone().requires_grad_()
    same=payload_kl(student,teacher)
    assert abs(float(same))<1e-6
    same.backward()
    assert torch.allclose(student.grad,torch.zeros_like(student),atol=1e-6)
    changed=torch.tensor([[-1.,0.,2.],[3.,1.,0.]],requires_grad=True)
    value=payload_kl(changed,teacher)
    assert float(value)>0
    value.backward()
    assert bool(torch.isfinite(changed.grad).all()) and bool((changed.grad!=0).any())


def test_payload_distillation_rejects_unnormalized_or_empty_teacher():
    torch=pytest.importorskip('torch')
    with pytest.raises(ValueError,match='normalized'):
        payload_kl(torch.zeros(2,3),torch.zeros(2,3))
    with pytest.raises(ValueError,match='nonempty'):
        payload_kl(torch.zeros(0,3),torch.zeros(0,3))


def test_common_and_added_probes_have_separate_denominators():
    rows=[dict(group='common_retention',correct=True),dict(group='common_retention',correct=False),
          dict(group='common_focused',correct=True),dict(group='additional_contrasts',correct=True)]
    assert metrics(rows)==dict(common_retention=dict(correct=1,total=2),common_focused=dict(correct=1,total=1),
                               additional_contrasts=dict(correct=1,total=1))


def test_payload_kl_cannot_backpropagate_into_frozen_teacher():
    torch=pytest.importorskip('torch')
    source=torch.tensor([[1.,2.,3.]],requires_grad=True)
    teacher=source.log_softmax(-1)
    student=torch.tensor([[3.,2.,1.]],requires_grad=True)
    payload_kl(student,teacher).backward()
    assert source.grad is None and student.grad is not None


def test_recorded_improvement_cannot_hide_a_lost_correct_reply(monkeypatch):
    from types import SimpleNamespace
    import benchmarks.engineering_balanced_training as module
    entries=[dict(source='retention',index=0,group='common_retention'),dict(source='focused',index=0,group='common_focused')]
    monkeypatch.setattr(module,'probe_entries',lambda *args:entries)
    old=dict(id='old',answer=dict(value=False))
    new=dict(id='new',answer=dict(value=True))
    data=SimpleNamespace(parent=SimpleNamespace(example=lambda i:old),example=lambda i:new)
    before=[dict(**entries[0],row_id='old',expected=old['answer'],completion='{"value":false}',correct=True),
            dict(**entries[1],row_id='new',expected=new['answer'],completion='{"value":false}',correct=False)]
    after=[dict(**entries[0],row_id='old',expected=old['answer'],completion='{"value":true}',correct=False),
           dict(**entries[1],row_id='new',expected=new['answer'],completion='{"value":true}',correct=True)]
    from benchmarks.engineering_focused_audit import regressions
    report=dict(before=before,after=after,before_metrics=metrics(before),after_metrics=metrics(after),
                changes=regressions(before,after),no_retention_regressions=False,all_probe_gate_passed=False,
                full_workflow_quality_gate_passed=False,inference_release=False)
    prior=dict(after=[dict(completion='{"value":false}')])
    validate_result(report,data,{},prior)
    report['no_retention_regressions']=True
    with pytest.raises(ValueError,match='quality promotion'):
        validate_result(report,data,{},prior)


def test_native_field_alignment_preserves_boolean_null_values():
    # A tiny offset tokenizer exercises the field mapper independently of Qwen.
    from benchmarks.engineering_regression_diagnosis import field_masks
    raw='{"a":false,"b":null}'
    class Tokenizer:
        def decode(self,ids,**kwargs):return ''.join(chr(i) for i in ids)
        def __call__(self,value,**kwargs):return dict(input_ids=[ord(c) for c in value],offset_mapping=[(i,i+1) for i in range(len(value))])
    masks=field_masks(Tokenizer(),[ord(c) for c in raw],dict(a=False,b=None))
    assert ''.join(c for c,m in zip(raw,masks['a']) if m)=='false'
    assert ''.join(c for c,m in zip(raw,masks['b']) if m)=='null'


def test_guard_backtracking_restores_adamw_before_each_retry():
    torch=pytest.importorskip('torch')
    from benchmarks.engineering_retention_guard import transactional_step
    model=torch.nn.Linear(1,1,bias=False)
    model.weight.data.zero_();model.weight.grad=torch.tensor([[-1.]])
    opt=torch.optim.AdamW(model.parameters(),lr=.1,weight_decay=0.)
    result=transactional_step(model,opt,lambda:dict(passed=float(model.weight.detach())<=.03),scales=(1.,.5,.25))
    assert result['accepted'] and result['accepted_scale']==.25 and len(result['attempts'])==3
    assert abs(float(model.weight.detach())-.025)<1e-6
    state=opt.state[model.weight]
    assert float(state['step'])==1 and abs(float(state['exp_avg'])+.1)<1e-6
    assert opt.param_groups[0]['lr']==.1


def test_guard_rejection_and_exception_restore_weights_and_empty_moments():
    torch=pytest.importorskip('torch')
    from benchmarks.engineering_retention_guard import transactional_step
    model=torch.nn.Linear(1,1,bias=False)
    model.weight.data.zero_();model.weight.grad=torch.tensor([[-1.]])
    opt=torch.optim.AdamW(model.parameters(),lr=.1,weight_decay=0.)
    result=transactional_step(model,opt,lambda:dict(passed=False),scales=(1.,.5))
    assert not result['accepted'] and float(model.weight.detach())==0 and not opt.state
    def fail():raise RuntimeError('failed witness kernel')
    with pytest.raises(RuntimeError,match='failed witness'):
        transactional_step(model,opt,fail)
    assert float(model.weight.detach())==0 and not opt.state and opt.param_groups[0]['lr']==.1


def test_generation_witness_stops_at_eos_not_template_newline():
    torch=pytest.importorskip('torch')
    from types import SimpleNamespace
    from benchmarks.engineering_retention_guard import witness_batch
    class Processor:
        tokenizer=SimpleNamespace(eos_token_id=9)
        def apply_chat_template(self,messages,add_generation_prompt,**kwargs):
            ids=[1,2] if add_generation_prompt else [1,2,3,4,9,10]
            return dict(input_ids=torch.tensor([ids]),attention_mask=torch.ones((1,len(ids)),dtype=torch.long))
    batch=witness_batch(Processor(),[dict(role='user',content='x')],'{}')
    assert batch['input_ids'].tolist()==[[1,2,3,4,9]]
    assert batch['labels'].tolist()==[[-100,-100,3,4,9]]

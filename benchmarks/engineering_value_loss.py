"""Verified payload-token metrics and an explicitly weighted assistant loss.

Only flat JSON primitive values are supported. Prompt/image tokens remain
masked. Token alignment must roundtrip exactly, otherwise admission fails.
"""
import json
import math


def value_spans(answer):
    if not isinstance(answer,dict) or not answer or any(not isinstance(k,str) or (v is not None and not isinstance(v,(str,int,float,bool))) for k,v in answer.items()):
        raise ValueError('nonempty flat primitive JSON answer required')
    text=json.dumps(answer,separators=(',',':'),allow_nan=False)
    decoder=json.JSONDecoder();cursor=1;spans=[]
    for i,(key,value) in enumerate(answer.items()):
        decoded,end=decoder.raw_decode(text,cursor)
        if decoded!=key or text[end]!=':':raise ValueError('JSON key alignment differs')
        start=end+1;actual,end=decoder.raw_decode(text,start)
        if isinstance(value,str):
            if not value:raise ValueError('empty string payload not qualified')
            spans.append((start+1,end-1))
        else:spans.append((start,end))
        cursor=end+1
    return text,spans


def payload_mask(tokenizer,token_ids,answer):
    ids=token_ids.tolist() if hasattr(token_ids,'tolist') else list(token_ids)
    raw=tokenizer.decode(ids,skip_special_tokens=False)
    text,spans=value_spans(answer)
    if not raw.startswith(text):raise ValueError('actual assistant content differs from flat target')
    encoded=tokenizer(raw,add_special_tokens=False,return_offsets_mapping=True)
    if list(encoded['input_ids'])!=ids:raise ValueError('assistant token offsets do not roundtrip')
    offsets=encoded['offset_mapping']
    if len(offsets)!=len(ids):raise ValueError('assistant token offsets missing')
    mask=[any(a<end and b>start for start,end in spans) for a,b in offsets]
    if not any(mask):raise ValueError('no aligned value-bearing target tokens')
    for start,end in spans:
        if not any(a<=start<b or start<=a<end for (a,b),active in zip(offsets,mask) if active):
            raise ValueError('a JSON value has no aligned token')
    return mask


def objective(logits,targets,mask,value_weight=4.):
    import torch
    if not isinstance(value_weight,(int,float)) or isinstance(value_weight,bool) or not math.isfinite(value_weight) or not 1<=value_weight<=16:
        raise ValueError('finite payload weight1..16 required')
    if logits.ndim!=3 or logits.shape[0]!=1 or targets.ndim!=1 or logits.shape[1]!=len(targets) or len(mask)!=len(targets):
        raise ValueError('selected causal target/logit alignment differs')
    critical=torch.as_tensor(mask,dtype=torch.bool,device=logits.device)
    if not bool(critical.any()):raise ValueError('payload tokens required')
    ce=torch.nn.functional.cross_entropy(logits[0].float(),targets,reduction='none')
    weights=1.+(float(value_weight)-1.)*critical.to(ce.dtype)
    loss=(weights*ce).sum()/weights.sum()
    if not bool(torch.isfinite(loss)):raise ValueError('nonfinite weighted assistant loss')
    predicted=logits[0].argmax(-1);prob=logits[0].float().softmax(-1).gather(1,targets[:,None]).squeeze(1)
    metrics=dict(mean_ce=float(ce.mean().detach()),payload_ce=float(ce[critical].mean().detach()),
                 syntax_ce=float(ce[~critical].mean().detach()) if bool((~critical).any()) else None,
                 payload_tokens=int(critical.sum()),assistant_tokens=len(targets),
                 payload_token_accuracy=float((predicted[critical]==targets[critical]).float().mean()),
                 mean_gold_payload_probability=float(prob[critical].mean().detach()),value_weight=float(value_weight))
    return loss,metrics

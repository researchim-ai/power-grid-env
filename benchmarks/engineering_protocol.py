"""Versioned public action transport for the pinned Qwen template; no hidden state."""
import copy
import hashlib
import json
import re
from pydantic import BaseModel,ConfigDict,Field,field_validator
from typing import Literal
from benchmarks.engineering_rollout import parse_action
from benchmarks.engineering_observations import referenced_messages


class RunContract(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    schema_version: Literal['engineering-run/v1']='engineering-run/v1'
    protocol: Literal['json-action-v1','qwen-native-v1']='json-action-v1'
    public_view: Literal['raw','compact-pf-v1']='compact-pf-v1'
    context_tokens: int=Field(default=16384,ge=1024,le=32768)
    action_tokens: int=Field(default=256,ge=32,le=512)
    maximum_actions: int=Field(default=12,ge=1,le=32)
    repeat_limit: int=Field(default=2,ge=0,le=4)
    enable_thinking: Literal[False]=False

    @field_validator('repeat_limit')
    @classmethod
    def valid_repeat_limit(cls,value):
        if value not in (0,2,3,4):raise ValueError('repeat limit must be disabled or2..4')
        return value

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.model_dump(),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def native_completion(action):
    # This native template has no XML escaping: reject delimiter-bearing scalars.
    # Supported task actions use identifiers/enums or bounded explanation strings.
    fields=[]
    for key,value in action['arguments'].items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):raise ValueError('invalid parameter name')
        if not isinstance(value,str) or '<' in value or '>' in value or value!=value.strip():
            raise ValueError('native scalar must be an unambiguous bounded string')
        fields.append('<parameter='+key+'>\n'+value+'\n</parameter>\n')
    return '<tool_call>\n<function='+action['name']+'>\n'+''.join(fields)+'</function>\n</tool_call>'


def parse_native(text,allowed):
    if not isinstance(text,str) or len(text.encode())>8192:raise ValueError('native action byte budget')
    match=re.fullmatch(r'\s*<tool_call>\s*<function=([A-Za-z_][A-Za-z0-9_]*)>\s*(.*?)\s*</function>\s*</tool_call>\s*',text,re.S)
    if not match:raise ValueError('one exact native tool call required')
    body=match[2];args={};cursor=0
    for item in re.finditer(r'<parameter=([A-Za-z_][A-Za-z0-9_]*)>\n?([^<>]*?)\n?</parameter>\s*',body,re.S):
        if body[cursor:item.start()].strip() or item[1] in args:raise ValueError('invalid/duplicate native parameters')
        args[item[1]]=item[2].strip();cursor=item.end()
    if body[cursor:].strip():raise ValueError('unparsed native payload')
    return parse_action(json.dumps(dict(name=match[1],arguments=args)),allowed)


def render_history(messages,contract):
    messages=referenced_messages(messages) if contract.public_view=='compact-pf-v1' else copy.deepcopy(messages)
    initial=json.loads(messages[1]['content'])
    initial['task']['tools']=[name for name in initial['task']['tools'] if name!='export']
    initial['task']['tool_schemas']=[s for s in initial['task']['tool_schemas'] if s['function']['name']!='export']
    messages[1]['content']=json.dumps(initial,ensure_ascii=False,separators=(',',':'))
    tools=initial['task']['tool_schemas']
    if contract.protocol=='json-action-v1':return messages,{}
    task=initial['task'];initial['task']={k:v for k,v in task.items() if k!='tool_schemas'}
    output=[dict(role='system',content='Solve the bounded power-grid task using the provided tools. Preserve owner requirements. Submit only after fresh physical validation.'),
            dict(role='user',content=json.dumps(initial,ensure_ascii=False,separators=(',',':')))]
    if (len(messages)-2)%2:raise ValueError('complete action/response history required')
    for i in range(2,len(messages),2):
        try:action=parse_action(messages[i]['content'],task['tools'])
        except (ValueError,TypeError):
            output.extend([dict(role='assistant',content=messages[i]['content']),dict(role='user',content=messages[i+1]['content'])])
            continue
        call='call_'+str((i-2)//2)
        output.append(dict(role='assistant',content='',tool_calls=[dict(id=call,type='function',function=dict(name=action['name'],arguments=action['arguments']))]))
        output.append(dict(role='tool',tool_call_id=call,name=action['name'],content=messages[i+1]['content']))
    return output,dict(tools=tools)


def pack_action(processor,messages,action,contract):
    import torch
    rendered,kwargs=render_history(messages,contract)
    settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False,**kwargs)
    prompt=processor.apply_chat_template(rendered,add_generation_prompt=True,**settings)
    if contract.protocol=='json-action-v1':
        completion=dict(role='assistant',content=json.dumps(action,ensure_ascii=False,separators=(',',':')))
    else:
        # Ensure renderer/parser agree before relying on template normalization.
        if parse_native(native_completion(action),json.loads(messages[1]['content'])['task']['tools'])!=action:
            raise ValueError('native action roundtrip differs')
        completion=dict(role='assistant',content='',tool_calls=[dict(id='target_call',type='function',function=action)])
    full=processor.apply_chat_template(rendered+[completion],add_generation_prompt=False,**settings)
    n=prompt['input_ids'].shape[1];total=full['input_ids'].shape[1]
    if total<=n or total>contract.context_tokens or total-n>contract.action_tokens:
        raise ValueError('context/action token budget exceeded')
    if not torch.equal(full['input_ids'][:,:n],prompt['input_ids']):raise ValueError('native template prefix changed')
    if not bool((full['attention_mask']==1).all()):raise ValueError('unpadded single-example contract required')
    labels=full['input_ids'].clone();labels[:,:n]=-100;full['labels']=labels
    return full,dict(prompt_tokens=n,label_tokens=total-n,total_tokens=total)

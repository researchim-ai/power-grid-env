"""Tagged objectives for a new text/visual editor-card curriculum.

Action choice,conditional argument serialization and public readiness are
separate tasks. Their success is not interchangeable with full episode success.
"""
import copy
import io
import json
from PIL import Image
from benchmarks.engineering_grounding import render_scene
from benchmarks.engineering_protocol import parse_native
from benchmarks.engineering_rollout import strict_json


def pack(processor,row,mode='text'):
    import torch
    if mode not in ('text','visual_hybrid'):raise ValueError('explicit card modality required')
    card=copy.deepcopy(row['card']);tools=card.pop('tools');objective=row['task'];answer=row['answer']
    if objective=='full_action':instruction='EDITOR-CARD/V1. Choose exactly one native tool call. Use visible public aliases,not raw equipment IDs. Preserve the goal,validate and obtain a fresh completed study. Construction and repair must satisfy every constraint;diagnosis reports the observed fault.'
    elif objective=='bind_arguments':instruction='ARGUMENT-BINDING/V1. The operation is already chosen. Return only its JSON arguments,using the tool schema and requested public aliases.'
    elif objective=='readiness':instruction='READINESS/V1. Return JSON stating whether the public study/solution is ready. For diagnosis assess study_ready;for construction/repair assess solution_ready against every declared constraint. Freshness alone is not feasibility.'
    else:raise ValueError('unknown card curriculum objective')
    if objective=='bind_arguments':card['given_tool']=row['given_tool'];card['directive']=row['directive'];card['argument_schema']=next(t['function']['parameters'] for t in tools if t['function']['name']==row['given_tool'])
    content=[]
    if mode=='visual_hybrid':
        scene=card.pop('scene');img=Image.open(io.BytesIO(render_scene(scene))).convert('RGB');content.append(dict(type='image',image=img))
    content.append(dict(type='text',text=json.dumps(card,ensure_ascii=False,separators=(',',':'))))
    messages=[dict(role='system',content=instruction),dict(role='user',content=content)]
    settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    if objective=='full_action':settings['tools']=tools
    prompt=processor.apply_chat_template(messages,add_generation_prompt=True,**settings)
    completion=dict(role='assistant',content=json.dumps(answer,separators=(',',':')))
    if objective=='full_action':completion=dict(role='assistant',content='',tool_calls=[dict(id='target',type='function',function=answer)])
    full=processor.apply_chat_template(messages+[completion],add_generation_prompt=False,**settings)
    n=prompt['input_ids'].shape[1];length=full['input_ids'].shape[1]
    if not n<length<=24576 or length-n>256 or not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('card prefix/context differs')
    for k in ('pixel_values','image_grid_thw'):
        if k in prompt and not torch.equal(prompt[k],full[k]):raise ValueError('card pixels changed when adding answer')
    labels=full['input_ids'].clone();labels[:,:n]=-100;full['labels']=labels
    raw=processor.tokenizer.decode(full['input_ids'][labels!=-100],skip_special_tokens=True)
    actual=parse_native(raw,[t['function']['name'] for t in tools]) if objective=='full_action' else strict_json(raw)
    if actual!=answer:raise ValueError('card labels lost action/argument/readiness semantics')
    return full,dict(task=objective,mode=mode,prompt_tokens=n,label_tokens=length-n,pixels_present='pixel_values' in full)

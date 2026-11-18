"""Provisional train-only action supervision, checked by the native tokenizer.

No weights are loaded. Owner teacher actions are labels, never heldout examples.
Not automatically eligible for gold SFT, visual training or GRPO.
"""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks import engineering_agent as env
from benchmarks.engineering_rollout import rollout, parse_action
from benchmarks.engineering_qwen_policy import pack
from power_grid_env.qwen35_training import local_target
from power_grid_env.vlm_batches import local_qwen35_processor
from power_grid_env.study_artifacts import seal


def export(processor, out, context_tokens=16384, target_sha256=None):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    rows=[]
    for task in env.registry():
        if task['split']!='train':continue
        actions=iter(env.teacher(task['id'])['log'])
        def teacher(messages):
            action=next(actions)
            text=json.dumps(dict(name=action['name'],arguments=action['arguments']),ensure_ascii=False,separators=(',',':'))
            parse_action(text,env.public_task(task)['tools'])
            prompt, n=pack(processor,messages,context_tokens,1)
            complete=processor.apply_chat_template(messages+[dict(role='assistant',content=text)],
                tokenize=True,add_generation_prompt=False,return_dict=True,return_tensors='pt',enable_thinking=False)
            ids=complete['input_ids'][0].tolist();prefix=prompt['input_ids'][0].tolist()
            if ids[:n]!=prefix:raise ValueError('assistant label is not an exact token prefix continuation')
            if len(ids)<=n or len(ids)>context_tokens:raise ValueError('invalid label/context budget')
            record=dict(task_id=task['id'],split='train',family_sha256=task['family_sha256'],
                messages=messages,completion=dict(role='assistant',content=text),
                prompt_tokens=n,label_tokens=len(ids)-n,total_tokens=len(ids),
                loss_mask=dict(prompt_tokens=0,assistant_completion=1),eligible_for_gold_sft=False)
            rows.append(record)
            return text
        result=rollout(task['id'],teacher)
        if not result['success']:raise ValueError('teacher replay or tokenizer qualification failed')
    with (root/'train-actions.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary=dict(schema='power-grid-env.engineering-action-supervision/v1',tasks=7,examples=len(rows),
        split='train',context_tokens=context_tokens,maximum_total_tokens=max(r['total_tokens'] for r in rows),
        model_target_sha256=target_sha256,
        episode_sha256=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest(),
        exporter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        eligible_for_gold_sft=False,weights_loaded=False,
        scope='Token-prefix-verified text/action teacher supervision only. No optimizer, images, independent label qualification or heldout inclusion.')
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');seal(root)
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True)
    p.add_argument('--out',required=True);p.add_argument('--context-tokens',type=int,default=16384)
    a=p.parse_args();target=local_target(a.target,a.target_sha256)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    print(json.dumps(export(processor,a.out,a.context_tokens,a.target_sha256)))

if __name__=='__main__':main()

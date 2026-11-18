"""Processor-only train teacher-prefix preflight, never a model accuracy score."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks import engineering_agent as env
from benchmarks.engineering_rollout import ProcessPolicy, rollout
from power_grid_env.study_artifacts import seal


def qualify(command, out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    policy=ProcessPolicy(command,timeout=180)
    rows=[]
    try:
        for task in env.registry():
            if task['split']!='train':continue
            actions=iter(env.teacher(task['id'])['log'])
            def teacher(messages):
                response=policy(messages)
                rows.append(dict(task_id=task['id'],prefix=len(messages),**response))
                action=next(actions)
                return json.dumps(dict(name=action['name'],arguments=action['arguments']))
            result=rollout(task['id'],teacher)
            if not result['success']:raise ValueError('processor/teacher preflight failed')
    finally:policy.close()
    summary=dict(schema='power-grid-env.engineering-context/v1',tasks=7,prefixes=len(rows),
        maximum_input_tokens=max(r['input_tokens'] for r in rows),rows=rows,
        candidate_command=command,
        episode_sha256=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest(),
        scope='Train teacher prefixes only; no truncation, weight inference, VLM or learned-agent qualification')
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');seal(root)
    return {k:v for k,v in summary.items() if k!='rows'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    if not cmd:p.error('processor-only worker command required')
    if '--processor-only' not in cmd:p.error('context preflight must not load model weights')
    print(json.dumps(qualify(cmd,a.out)))

if __name__=='__main__':main()

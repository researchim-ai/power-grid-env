"""Build/replay train-only diagnostic episodes, optionally from an isolated wheel."""
import argparse
import importlib.abc
import json
from pathlib import Path
import sys


def run(qualified,accepted,out,installed_dir=None):
    if installed_dir:
        installed=Path(installed_dir).resolve();sys.path.insert(0,str(installed))
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self,fullname,path=None,target=None):
                if fullname.split('.')[0] in {'benchmarks','tests','andes','motulator'}:raise ImportError('reference import blocked')
        sys.meta_path.insert(0,Block())
    import power_grid_env.recovery_curriculum as curriculum
    from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
    from power_grid_env.evidence import package_fingerprint
    if installed_dir and not Path(curriculum.__file__).resolve().is_relative_to(installed):raise ValueError('curriculum outside installed wheel')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    receipt=curriculum.build(qualified,accepted,root/'corpus');anchor=receipt['manifest_sha256']
    corpus=curriculum.verify_corpus(root/'corpus',anchor)
    for name in ('sft','prompts'):curriculum.export(root/'corpus',root/(name+'.jsonl'),anchor,name)
    examples=[json.loads(row) for row in (root/'sft.jsonl').read_text().splitlines()]
    prompts=[json.loads(row) for row in (root/'prompts.jsonl').read_text().splitlines()]
    if len(examples)!=18 or len(prompts)!=18:raise ValueError('export inventory differs')
    rows=[]
    for task,example,prompt in zip(corpus['rows'],examples,prompts):
        if 'expected' in prompt or 'reward' in prompt or prompt['observation']!=task['observation'] or prompt['metadata']['split']!='train':raise ValueError('privileged prompt data')
        if example['tools']!=curriculum.TOOL_SCHEMAS or example['metadata']['family_id']!=task['family_id']:raise ValueError('tool/family metadata differs')
        session=curriculum.ResetDiagnosticSession(task['observation']);messages=example['messages']
        if len(messages)!=6:raise ValueError('unexpected teacher episode shape')
        for assistant,tool in zip(messages[1:5:2],messages[2:5:2]):
            call=assistant['tool_calls'][0];fn=call['function']
            actual=session.call(fn['name'],json.loads(fn['arguments']))
            if tool['tool_call_id']!=call['id'] or encoded(actual)!=encoded(json.loads(tool['content'])):raise ValueError('tool replay differs')
        answer=json.loads(messages[-1]['content'])
        positive=curriculum.score(root/'corpus',task['id'],answer,anchor)
        wrong=dict(action='hold',reason='not_latched') if answer['action']=='request_reset' else dict(action='request_reset',reason='eligible')
        negative=curriculum.score(root/'corpus',task['id'],wrong,anchor)
        malformed=curriculum.score(root/'corpus',task['id'],answer|dict(reset_level=1.),anchor)
        result=session.call('submit_decision',answer)
        if positive['reward']!=1. or negative['reward']!=0. or malformed['reward']!=0. or not result['ok'] or result['physical_action_executed']:raise ValueError('diagnostic grading/action boundary differs')
        rows.append(dict(id=task['id'],passed=True,positive_reward=1.,wrong_reward=0.,malformed_reward=0.))
    try:curriculum.score(root/'corpus',corpus['rows'][0]['id'],{},'untrusted_manifest')
    except ValueError:pass
    else:raise ValueError('owner anchor bypassed')
    if installed_dir and any(not Path(m.__file__).resolve().is_relative_to(installed) for name,m in sys.modules.items() if name.startswith('power_grid_env') and getattr(m,'__file__',None)):raise ValueError('mixed runtime imports')
    data=dict(passed=True,code_sha256=package_fingerprint(),corpus_manifest_sha256=anchor,rows=rows,
        train_episodes=18,families=len({r['family_id'] for r in corpus['rows']}),split='train',
        sft_sha256=digest((root/'sft.jsonl').read_bytes()),prompts_sha256=digest((root/'prompts.jsonl').read_bytes()),
        runtime_file=curriculum.__file__,reference_imports_blocked=bool(installed_dir),
        independent_host=False,model_trained=False,VLM_images=False,scope='Scripted read-only tool diagnosis from pre-command indications; diagnostic reward, not closed-loop control or design success.')
    (root/'receipt.json').write_bytes(encoded(data));seal(root)
    return {k:v for k,v in data.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qualified',required=True);p.add_argument('--accepted',required=True);p.add_argument('--out',required=True);p.add_argument('--installed-dir')
    print(run(**vars(p.parse_args())))

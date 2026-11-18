"""All-target processor admission of audited actual-student corrections."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_student_dataset import StudentCorrectionDataset,source_sha
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal


def qualify(corpus,manifest,train,train_manifest,suite,suite_manifest,student_eval,student_manifest,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh correction preflight required')
    data=StudentCorrectionDataset(corpus,manifest,train,train_manifest,suite,suite_manifest,student_eval,student_manifest)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    rows=[];failures=[];actions=Counter();kinds=Counter();identities=set()
    if not len(data):failures.append(dict(index=None,error='no successful correction targets'))
    for index in range(len(data)):
        try:
            batch,meta=data.batch(processor,index);messages,action,_,_=data.example(index)
            identity=hashlib.sha256(json.dumps(dict(messages=messages,action=action),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            identities.add(identity);rows.append(dict(**meta,public_target_sha256=identity));actions[meta['action']]+=1;kinds[meta['kind']]+=1
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=index,error=str(exc)[:1000]))
        if (index+1)%32==0:print(json.dumps(dict(checked=index+1,total=len(data),failures=len(failures))),flush=True)
    result=dict(schema='power-grid-env.student-correction-preflight/v1',passed=bool(rows) and not failures,targets=len(data),checked_targets=len(rows),failures=failures,
        anchors=data.anchors,target_sha256=target_sha,contract=data.contract.model_dump(),contract_sha256=data.contract.fingerprint(),
        actions=dict(actions),kinds=dict(kinds),unique_public_targets=len(identities),maximum_prompt_tokens=max((r['prompt_tokens'] for r in rows),default=0),
        maximum_label_tokens=max((r['label_tokens'] for r in rows),default=0),supervised_tokens=sum(r['label_tokens'] for r in rows),
        loader_sha256=source_sha('engineering_student_dataset.py'),teacher_sha256=source_sha('engineering_student_recovery.py'),protocol_sha256=source_sha('engineering_protocol.py'),
        weights_loaded=False,eligible_for_gold_sft=False,rows=rows,scope='All actual-student correction targets,replay/prefix/mask/token admission;not a new model update or learned recovery score')
    write_json(root/'summary.json',result);seal(root);return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('corpus','manifest-sha256','train','train-manifest-sha256','suite','suite-manifest-sha256','student-evaluation','student-manifest-sha256','target','target-sha256','out'):p.add_argument('--'+flag,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.corpus,a.manifest_sha256,a.train,a.train_manifest_sha256,a.suite,a.suite_manifest_sha256,a.student_evaluation,a.student_manifest_sha256,a.target,a.target_sha256,a.out)))

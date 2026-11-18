"""Standalone strict-profile evaluation of an experimental mixture checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset
from benchmarks.engineering_learning_gate import strict_contract,load_suite,load_evaluation,evaluate_cases
from benchmarks.engineering_rollout import ProcessPolicy
from benchmarks.engineering_v3_policy import receipt


def evaluate(python,target,target_sha,adapter,adapter_manifest,corpus,corpus_manifest,out,split='train',suite=None,suite_manifest=None):
    receipt(adapter,adapter_manifest,target_sha);contract=strict_contract()
    if split=='train':
        if not suite or not suite_manifest:raise ValueError('train evaluation requires frozen suite')
        data=ProceduralDataset(corpus,corpus_manifest,contract);s=load_suite(data,suite,suite_manifest);cases=[data.scenarios[t] for t in s['task_ids']]
        source=dict(role='tiny_train',corpus_manifest_sha256=corpus_manifest,suite_manifest_sha256=suite_manifest)
    else:
        if suite or suite_manifest:raise ValueError('heldout evaluation cannot select train suite')
        cases=load_evaluation(corpus,corpus_manifest,split);source=dict(role=split,corpus_manifest_sha256=corpus_manifest)
    command=[python,'-m','benchmarks.engineering_v3_policy','--target',str(target),'--target-sha256',target_sha,
        '--adapter',str(adapter),'--adapter-manifest-sha256',adapter_manifest]
    policy=ProcessPolicy(command,timeout=180)
    candidate=dict(kind='native_model',target_sha256=target_sha,adapter_manifest_sha256=adapter_manifest,transport=policy.identity,
        v3_policy_source_sha256=hashlib.sha256(Path(__file__).with_name('engineering_v3_policy.py').read_bytes()).hexdigest(),
        training_phase='experimental_admitted_text_mixture_no_quality_gate')
    try:return evaluate_cases(cases,policy,out,contract,source,candidate)
    finally:policy.close()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('python','target','target-sha256','adapter','adapter-manifest-sha256','corpus','manifest-sha256','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--split',choices=['train','dev','test'],default='train');p.add_argument('--suite');p.add_argument('--suite-manifest-sha256')
    a=p.parse_args();print(json.dumps(evaluate(a.python,a.target,a.target_sha256,a.adapter,a.adapter_manifest_sha256,a.corpus,a.manifest_sha256,a.out,a.split,a.suite,a.suite_manifest_sha256)))

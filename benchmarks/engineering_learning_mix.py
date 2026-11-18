"""Explicitly admitted text mixtures; planning never runs an optimizer.

Both corpora must share target/protocol and pass complete all-target checks.
Source/kind/family/action-balanced sampling is a proposed recipe,not measured
optimal weighting or an assertion that the policy learned recovery.
"""
from collections import defaultdict,Counter
import hashlib
import json
from pathlib import Path
import random
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_student_dataset import StudentCorrectionDataset,source_sha
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_trainer_v2 import check_preflight
from power_grid_env.study_artifacts import read_json,verify,seal


def check_student_preflight(root,anchor,data,target_sha):
    if verify(root)!=anchor:raise ValueError('owner correction preflight differs')
    r=read_json(Path(root)/'summary.json')
    if (r.get('schema')!='power-grid-env.student-correction-preflight/v1' or r.get('passed') is not True or r.get('failures') or not len(data) or
        r.get('targets')!=len(data) or r.get('checked_targets')!=len(data) or r.get('anchors')!=data.anchors or r.get('target_sha256')!=target_sha or
        r.get('contract')!=data.contract.model_dump() or r.get('contract_sha256')!=data.contract.fingerprint() or
        r.get('loader_sha256')!=source_sha('engineering_student_dataset.py') or r.get('teacher_sha256')!=source_sha('engineering_student_recovery.py') or
        r.get('protocol_sha256')!=source_sha('engineering_protocol.py')):raise ValueError('complete correction preflight does not match this learning profile')


class AdmittedTextMixture:
    def __init__(self,procedural,corrections):
        if procedural.contract!=corrections.contract or procedural.contract!=strict_contract():raise ValueError('mixture requires identical strict24k profiles')
        if not len(procedural) or not len(corrections):raise ValueError('nonempty admitted sources required')
        if corrections.anchors['train_manifest_sha256']!=procedural.manifest_sha256:raise ValueError('corrections came from a different train corpus')
        self.datasets={'procedural':procedural,'student_correction':corrections};self.contract=procedural.contract
        self.groups={}
        for source,data in self.datasets.items():
            groups=defaultdict(lambda:defaultdict(list))
            for index,(e,t) in enumerate(data.targets):
                episode=data.episodes[e];event=episode['events'][t]
                if episode['split']!='train' or event['source']!='public_expert':raise ValueError('heldout or student action in mixture labels')
                kind=data.scenarios[episode['task_id']]['kind'];key=(kind,episode['family_sha256'],event['action']['name'])
                groups[key][e].append(index)
            self.groups[source]=groups

    def schedule(self,steps,seed=42):
        if type(steps) is not int or not 2<=steps<=100000:raise ValueError('mixture step budget must be2..100000')
        if type(seed) is not int or not 0<=seed<2**32:raise ValueError('bounded integer seed required')
        rng=random.Random(seed);sources=['procedural','student_correction']*(steps//2)
        if steps%2:sources.append('procedural')
        rng.shuffle(sources);queues={};result=[]
        for source in sources:
            if not queues.get(source):
                queues[source]=sorted(self.groups[source]);rng.shuffle(queues[source])
            key=queues[source].pop();episodes=self.groups[source][key];episode=rng.choice(sorted(episodes));index=rng.choice(episodes[episode])
            result.append(dict(source=source,index=index,kind=key[0],family_sha256=key[1],action=key[2]))
        return result

    def batch(self,processor,entry):
        if set(entry)!= {'source','index','kind','family_sha256','action'} or entry['source'] not in self.datasets:raise ValueError('invalid mixture schedule entry')
        data=self.datasets[entry['source']];index=entry['index']
        if type(index) is not int or not 0<=index<len(data):raise ValueError('mixture target outside source')
        e,t=data.targets[index];episode=data.episodes[e];event=episode['events'][t]
        expected=(data.scenarios[episode['task_id']]['kind'],episode['family_sha256'],event['action']['name'])
        if (entry['kind'],entry['family_sha256'],entry['action'])!=expected:raise ValueError('mixture schedule label identity differs')
        batch,meta=data.batch(processor,index)
        return batch,dict(**meta,corpus_source=entry['source'])


def plan(spec_path,spec_sha,out,steps=128,seed=42):
    path=Path(spec_path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=spec_sha:raise ValueError('owner mixture source specification differs')
    spec=read_json(path)
    required={'train','train_manifest','suite','suite_manifest','student_eval','student_eval_manifest','corrections','corrections_manifest',
        'train_preflight','train_preflight_manifest','student_preflight','student_preflight_manifest','target_sha256'}
    if set(spec)!=required:raise ValueError('explicit complete mixture source specification required')
    root=Path(out)
    if root.exists():raise ValueError('fresh mixture plan required')
    procedural=ProceduralDataset(spec['train'],spec['train_manifest'],strict_contract())
    corrections=StudentCorrectionDataset(spec['corrections'],spec['corrections_manifest'],spec['train'],spec['train_manifest'],spec['suite'],spec['suite_manifest'],spec['student_eval'],spec['student_eval_manifest'])
    check_preflight(spec['train_preflight'],spec['train_preflight_manifest'],procedural,spec['train_manifest'],spec['target_sha256'],strict_contract())
    check_student_preflight(spec['student_preflight'],spec['student_preflight_manifest'],corrections,spec['target_sha256'])
    data=AdmittedTextMixture(procedural,corrections);schedule=data.schedule(steps,seed)
    result=dict(schema='power-grid-env.admitted-text-mixture-plan/v1',spec_sha256=spec_sha,sources=spec,target_sha256=spec['target_sha256'],
        contract=strict_contract().model_dump(),steps=steps,seed=seed,source_counts=dict(Counter(e['source'] for e in schedule)),
        available_targets={source:len(d) for source,d in data.datasets.items()},schedule=schedule,
        sampling='approximately50/50 source;kind/family/action groups;episode uniform;expert action uniform;with replacement',
        duplicate_schedule_targets=steps-len({(r['source'],r['index']) for r in schedule}),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),optimizer_run=False,learned_recovery_claim=False,
        eligible_for_gold_sft=False,scope='Pinned admitted text training plan;weighting is provisional;no model update or quality score')
    write_json(root/'summary.json',result);seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--spec-sha256',required=True);p.add_argument('--out',required=True)
    p.add_argument('--steps',type=int,default=128);p.add_argument('--seed',type=int,default=42)
    a=p.parse_args();print(json.dumps(plan(a.spec,a.spec_sha256,a.out,a.steps,a.seed)))

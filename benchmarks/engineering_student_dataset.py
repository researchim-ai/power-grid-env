"""Audited train-only student corrections and native next-expert batches.

Admission replays every attempted branch, including unsuccessful branches.
Student actions remain context, and terminal failures never become targets.
"""
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset
from benchmarks.engineering_learning_gate import load_suite,strict_contract
from benchmarks.engineering_learning_compare import audit
from benchmarks.engineering_student_recovery import correction,expert_messages
from benchmarks.engineering_protocol import pack_action
from power_grid_env.study_artifacts import read_json,verify


def source_sha(name):return hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()


class StudentCorrectionDataset:
    def __init__(self,corpus,manifest,train,train_manifest,suite,suite_manifest,student_eval,student_manifest,contract=None):
        self.contract=contract or strict_contract();self.root=Path(corpus);self.manifest_sha256=manifest
        if self.contract!=strict_contract():raise ValueError('student corrections require the qualified strict24k profile')
        if verify(corpus)!=manifest:raise ValueError('owner correction manifest differs')
        summary=read_json(self.root/'summary.json')
        expected=dict(schema='power-grid-env.student-corrections/v1',train_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest,
            student_evaluation_manifest_sha256=student_manifest,teacher_sha256=source_sha('engineering_student_recovery.py'),eligible_for_gold_sft=False)
        if any(summary.get(k)!=v for k,v in expected.items()):raise ValueError('correction source/teacher revision differs')
        data=ProceduralDataset(train,train_manifest,self.contract);selected=load_suite(data,suite,suite_manifest)
        cases=[data.scenarios[t] for t in selected['task_ids']];report,rows=audit(student_eval,student_manifest,cases,self.contract)
        if report['source']!=dict(role='tiny_train',corpus_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest) or report['candidate'].get('kind')!='native_model':
            raise ValueError('audited native model TRAIN states required')
        if summary.get('source_candidate')!=report['candidate']:raise ValueError('student candidate identity differs')
        attempts=[];episodes=[];files={}
        for case,row in zip(cases,rows):
            for n,t in enumerate(row['turns'],1):
                if 'completion' not in t:
                    attempts.append(dict(task_id=case['id'],prefix_calls=n-1,status='transport_failure_no_action',success=False));continue
                result=correction(case,row['turns'],n)
                if result is None:
                    attempts.append(dict(task_id=case['id'],prefix_calls=n,status='terminal_or_budget_exhausted',success=False));continue
                identity=hashlib.sha256(json.dumps(dict(student=student_manifest,task=case['id'],prefix=n),sort_keys=True).encode()).hexdigest()
                result['episode_id']=identity;files[identity]=result
                attempts.append(dict(task_id=case['id'],prefix_calls=n,episode_id=identity,status='corrected' if result['success'] else 'continuation_failed',success=result['success']))
                if result['success']:episodes.append(result)
        checks=dict(tasks=len(cases),source_turns=sum(len(r['turns']) for r in rows),visited_prefixes=sum(a['status']!='transport_failure_no_action' for a in attempts),
            successful_corrections=len(episodes),expert_targets=sum(e['expert_targets'] for e in episodes),attempts=attempts)
        if any(summary.get(k)!=v for k,v in checks.items()):raise ValueError('complete attempted-branch coverage/counts differ')
        if {p.stem for p in (self.root/'attempts').glob('*.json')}!=set(files):raise ValueError('correction attempt inventory differs')
        for identity,result in files.items():
            if read_json(self.root/'attempts'/(identity+'.json'))!=result:raise ValueError('student/expert source or physical replay differs')
        stored=[json.loads(line) for line in (self.root/'episodes.jsonl').read_text().splitlines()]
        if stored!=episodes:raise ValueError('positive correction episode inventory differs')
        self.episodes=episodes;self.scenarios=data.scenarios;self.summary=summary;self.targets=[]
        for e,episode in enumerate(episodes):
            self.targets.extend((e,t['turn']) for t in episode['events'] if t['source']=='public_expert')
        if len(self.targets)!=summary['expert_targets']:raise ValueError('expert target denominator differs')
        self.anchors=dict(correction_manifest_sha256=manifest,train_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest,student_evaluation_manifest_sha256=student_manifest)

    def __len__(self):return len(self.targets)

    def example(self,index):
        if type(index) is not int or not 0<=index<len(self):raise ValueError('correction target outside admitted corpus')
        e,turn=self.targets[index];episode=self.episodes[e]
        messages,action=next((m,a) for (m,a),t in zip(expert_messages(episode),(t for t in episode['events'] if t['source']=='public_expert')) if t['turn']==turn)
        return messages,action,episode,turn

    def batch(self,processor,index):
        messages,action,episode,turn=self.example(index);batch,meta=pack_action(processor,messages,action,self.contract)
        return batch,dict(index=index,task_id=episode['task_id'],episode_id=episode['episode_id'],family_sha256=episode['family_sha256'],
            prefix_calls=episode['prefix_calls'],source='public_expert',context_source='actual_student_rollout',turn=turn,
            action=action['name'],kind=self.scenarios[episode['task_id']]['kind'],**meta)

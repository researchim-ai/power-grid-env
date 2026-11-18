import copy
from collections import Counter
from types import SimpleNamespace
from pathlib import Path
import pytest
from benchmarks.engineering_learning_mix import AdmittedTextMixture,check_student_preflight
from benchmarks.engineering_student_dataset import source_sha
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal


class Data:
    def __init__(self):
        self.contract=strict_contract();self.manifest_sha256='a'*64;self.anchors={'train_manifest_sha256':'a'*64}
        self.episodes=[dict(task_id='task'+str(i),family_sha256='f'+str(i),split='train',events=[dict(source='public_expert',action=dict(name=name,arguments={}))]) for i,name in enumerate(['inspect_project','simulate','submit'])]
        self.scenarios={e['task_id']:dict(kind=('assemble','diagnose','repair')[i]) for i,e in enumerate(self.episodes)}
        self.targets=[(i,0) for i in range(3)]
    def __len__(self):return len(self.targets)
    def batch(self,processor,index):return dict(labels=index),dict(index=index,source='public_expert')


def test_balanced_mixture_is_exactly_source_balanced_deterministic_and_label_safe():
    a=Data();b=Data();mix=AdmittedTextMixture(a,b);order=mix.schedule(12)
    assert order==mix.schedule(12) and Counter(e['source'] for e in order)==dict(procedural=6,student_correction=6)
    for source in mix.datasets:
        first=[e for e in order if e['source']==source][:3];assert len({e['action'] for e in first})==3
    for entry in order:
        batch,meta=mix.batch(None,entry);assert batch['labels']==entry['index'] and meta['corpus_source']==entry['source']
    forged=dict(order[0],action='remove_line')
    with pytest.raises(ValueError):mix.batch(None,forged)
    with pytest.raises(ValueError):mix.schedule(True)
    with pytest.raises(ValueError):mix.schedule(1)


@pytest.mark.parametrize('change',['contract','anchor','heldout','student'])
def test_unmatched_or_untrusted_labels_cannot_mix(change):
    a=Data();b=Data()
    if change=='contract':b.contract=b.contract.model_copy(update={'context_tokens':16384})
    elif change=='anchor':b.anchors['train_manifest_sha256']='b'*64
    elif change=='heldout':b.episodes[0]['split']='test'
    else:b.episodes[0]['events'][0]['source']='student'
    with pytest.raises(ValueError):AdmittedTextMixture(a,b)


def test_student_preflight_is_bound_to_every_target_source_and_protocol(tmp_path):
    data=Data();r=dict(schema='power-grid-env.student-correction-preflight/v1',passed=True,failures=[],targets=3,checked_targets=3,
        anchors=data.anchors,target_sha256='t'*64,contract=data.contract.model_dump(),contract_sha256=data.contract.fingerprint(),
        loader_sha256=source_sha('engineering_student_dataset.py'),teacher_sha256=source_sha('engineering_student_recovery.py'),protocol_sha256=source_sha('engineering_protocol.py'))
    write_json(tmp_path/'ok'/'summary.json',r);check_student_preflight(tmp_path/'ok',seal(tmp_path/'ok'),data,'t'*64)
    for i,change in enumerate(['targets','target_sha256','passed','loader_sha256']):
        bad=copy.deepcopy(r);bad[change]=False if change=='passed' else 0 if change=='targets' else 'bad';root=tmp_path/str(i);write_json(root/'summary.json',bad)
        with pytest.raises(ValueError):check_student_preflight(root,seal(root),data,'t'*64)

import copy
import hashlib
import json
from pathlib import Path
import pytest
import benchmarks.engineering_trainer_v3 as module
from benchmarks.engineering_learning_mix import AdmittedTextMixture
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal


class Data:
    def __init__(self,*args):
        self.contract=strict_contract();self.manifest_sha256='a'*64;self.anchors={'train_manifest_sha256':'a'*64}
        self.targets=[(0,0)];self.episodes=[dict(split='train',task_id='t',family_sha256='f',events=[dict(source='public_expert',action=dict(name='simulate',arguments={}))])]
        self.scenarios={'t':dict(kind='repair')}
    def __len__(self):return 1


def prepared(tmp_path,monkeypatch):
    for name in ('ProceduralDataset','StudentCorrectionDataset'):monkeypatch.setattr(module,name,Data)
    monkeypatch.setattr(module,'check_preflight',lambda *a:None);monkeypatch.setattr(module,'check_student_preflight',lambda *a:None)
    spec=dict(train='train',train_manifest='a'*64,corrections='corrections',corrections_manifest='b'*64,suite='suite',suite_manifest='c'*64,
        student_eval='eval',student_eval_manifest='d'*64,train_preflight='tp',train_preflight_manifest='e'*64,student_preflight='sp',student_preflight_manifest='f'*64,target_sha256='t'*64)
    mix=AdmittedTextMixture(Data(),Data());r=dict(schema='power-grid-env.admitted-text-mixture-plan/v1',target_sha256='t'*64,contract=strict_contract().model_dump(),optimizer_run=False,
        source_sha256=module.source_sha('engineering_learning_mix.py'),spec_sha256='1'*64,sources=spec,steps=4,seed=42,schedule=mix.schedule(4),source_counts=dict(procedural=2,student_correction=2),duplicate_schedule_targets=2,available_targets=dict(procedural=1,student_correction=1))
    return r


def test_load_plan_rechecks_schedule_target_sources_and_real_admission(tmp_path,monkeypatch):
    r=prepared(tmp_path,monkeypatch);write_json(tmp_path/'ok'/'summary.json',r)
    mix,p=module.load_plan(tmp_path/'ok',seal(tmp_path/'ok'),'t'*64);assert len(p['schedule'])==4
    def failed(*a):raise ValueError('source preflight failed')
    monkeypatch.setattr(module,'check_student_preflight',failed)
    with pytest.raises(ValueError):module.load_plan(tmp_path/'ok',module.verify(tmp_path/'ok'),'t'*64)


@pytest.mark.parametrize('change',['schedule','target','profile','inventory','implementation'])
def test_resealed_schedule_or_inventory_drift_is_rejected(tmp_path,monkeypatch,change):
    r=prepared(tmp_path,monkeypatch)
    if change=='schedule':r['schedule'][0]['action']='submit'
    elif change=='target':r['target_sha256']='other'
    elif change=='profile':r['contract']['maximum_actions']=8
    elif change=='inventory':r['available_targets']['procedural']=2
    else:r['source_sha256']='bad'
    write_json(tmp_path/'bad'/'summary.json',r)
    with pytest.raises(ValueError):module.load_plan(tmp_path/'bad',seal(tmp_path/'bad'),'t'*64)

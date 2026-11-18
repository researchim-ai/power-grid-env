import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.engineering_v3_campaign import finish,compare_revisions
from benchmarks.engineering_learning_gate import strict_contract,evaluate_cases
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_recovery import RecoveryPolicy
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,seal,read_json


def test_revision_comparison_replays_both_policies_and_separates_schema_errors(tmp_path):
    cases=generate_scenarios(420,1,(1,1,1))[:3];source=dict(role='tiny_train');expert=RecoveryPolicy()
    evaluate_cases(cases,lambda m:'bad',tmp_path/'reference',strict_contract(),source,dict(kind='native_model',target_sha256='t',adapter_manifest_sha256='a'))
    evaluate_cases(cases,lambda m:json.dumps(expert(m)),tmp_path/'candidate',strict_contract(),source,dict(kind='native_model',target_sha256='t',adapter_manifest_sha256='b'))
    r=compare_revisions(tmp_path/'reference',verify(tmp_path/'reference'),tmp_path/'candidate',verify(tmp_path/'candidate'),cases,tmp_path/'comparison')
    assert r['tasks']==r['improved']==r['candidate_successes']==3 and r['reference_successes']==0 and r['physical_replay_passed']
    assert r['reference_action_counts']['wire_errors']==72 and r['candidate_action_counts']['schema_valid_calls']>0


def test_rejected_lifecycle_never_trains(tmp_path,monkeypatch):
    import benchmarks.engineering_v3_campaign as m
    write_json(tmp_path/'pilot'/'summary.json',dict(complete=False));seal(tmp_path/'pilot')
    def forbidden(*a,**k):raise AssertionError('no training before gate')
    monkeypatch.setattr(m,'train',forbidden)
    with pytest.raises(ValueError):finish(tmp_path/'pilot','admission','target','t','init','i','reference',tmp_path/'out')
    assert verify(tmp_path/'out') and read_json(tmp_path/'out'/'failure.json')['failed_phase']=='wait_for_native_lifecycle_pilot'


def test_chunk_chain_keeps_same_plan_and_initialization_then_restores_every_chunk(tmp_path,monkeypatch):
    import benchmarks.engineering_v3_campaign as m
    admission=tmp_path/'admission';write_json(admission/'summary.json',{});write_json(admission/'mixture-plan'/'summary.json',{});seal(admission/'mixture-plan');seal(admission)
    pilot=tmp_path/'pilot';write_json(pilot/'summary.json',dict(schema='power-grid-env.v3-native-lifecycle-pilot/v1',complete=True,adapter_optimizer_resume_exact=True,
        warm_start_verified=True,proxy_loaded_and_generated=True,frozen_base_unchanged=True,adapter_roundtrip_exact=True,target_sha256='t',initial_v2_manifest_sha256='i',admission_manifest_sha256=verify(admission)));write_json(pilot/'resumed'/'summary.json',{});seal(pilot/'resumed');seal(pilot)
    reference=tmp_path/'reference';write_json(reference/'summary.json',dict(candidate=dict(adapter_manifest_sha256='i')));seal(reference)
    monkeypatch.setattr(m,'compare_tensors',lambda *a:None);monkeypatch.setattr(m,'checkpoint_receipt',lambda *a:{})
    spec=dict(train='train',train_manifest='a',suite='suite',suite_manifest='s');monkeypatch.setattr(m,'load_plan',lambda *a:(None,dict(steps=128,sources=spec)))
    calls=[]
    def train(*args,**kwargs):
        previous=kwargs['resume'];cursor=0 if previous is None else read_json(Path(previous)/'summary.json')['completed_steps']
        if previous is not None:assert kwargs['resume_manifest']==verify(previous)
        assert kwargs['initial']=='init' and kwargs['initial_manifest']=='i'
        calls.append((cursor,previous));root=args[4];r=dict(completed_steps=min(cursor+kwargs['max_new_steps'],128));write_json(root/'summary.json',r);seal(root);return r
    monkeypatch.setattr(m,'train',train)
    def evaluate(*args,**kwargs):write_json(args[7]/'summary.json',dict(complete=True));seal(args[7]);return {}
    monkeypatch.setattr(m,'evaluate',evaluate);monkeypatch.setattr(m,'ProceduralDataset',lambda *a:SimpleNamespace(scenarios={'t':dict(id='t')}))
    monkeypatch.setattr(m,'load_suite',lambda *a:dict(task_ids=['t']))
    def compare(*args):
        root=args[-1];r=dict(tasks=1,reference_successes=0,candidate_successes=0,tiny_train_gate_passed=False);write_json(root/'summary.json',r);seal(root);return r
    monkeypatch.setattr(m,'compare_revisions',compare)
    r=finish(pilot,admission,'target','t','init','i',reference,tmp_path/'out',chunk_size=8)
    assert [c for c,p in calls]==list(range(0,128,8)) and len(calls)==16 and r['complete']
    assert r['tiny_train_gate_passed'] is False and verify(tmp_path/'out')


def test_campaign_requires_finite_chunks_and_owned_continuation(tmp_path):
    with pytest.raises(ValueError):finish('p','a','t','h','i','s','r',tmp_path/'bad',chunk_size=0)
    with pytest.raises(ValueError):finish('p','a','t','h','i','s','r',tmp_path/'bad',wait_seconds=0)
    with pytest.raises(ValueError):finish('p','a','t','h','i','s','r',tmp_path/'bad',resume='checkpoint')

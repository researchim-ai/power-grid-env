import hashlib
from pathlib import Path
import pytest
from benchmarks.engineering_learning_campaign import wait_sealed,validate_training,finish
from benchmarks.engineering_learning_gate import strict_contract,epoch_schedule
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal,verify


def test_wait_does_not_accept_partial_or_tampered_artifacts(tmp_path):
    import time
    write_json(tmp_path/'partial'/'summary.json',dict(complete=True))
    with pytest.raises(TimeoutError):wait_sealed(tmp_path/'partial',time.monotonic())
    seal(tmp_path/'partial');assert wait_sealed(tmp_path/'partial',time.monotonic())==verify(tmp_path/'partial')
    write_json(tmp_path/'partial'/'summary.json',dict(complete=False))
    with pytest.raises(ValueError):wait_sealed(tmp_path/'partial',time.monotonic())


def test_campaign_refuses_to_watch_an_unpinned_recipe_or_infinite_wait(tmp_path):
    write_json(tmp_path/'train'/'recipe.json',dict(epochs=1))
    args=(tmp_path/'train','a'*64,'base','data','b'*64,'suite','c'*64,tmp_path/'out','python','target','d'*64)
    with pytest.raises(ValueError):finish(*args,wait_seconds=0)
    with pytest.raises(ValueError):finish(*args)
    assert not (tmp_path/'out').exists()


def test_checkpoint_must_cover_the_exact_full_tiny_schedule(tmp_path,monkeypatch):
    import benchmarks.engineering_v2_adapter as adapter
    suite=dict(task_ids=['task'],target_indices=[1,2,3])
    recipe=dict(schema='power-grid-env.tiny-train-run/v1',train_manifest_sha256='a'*64,suite_manifest_sha256='b'*64,target_sha256='c'*64,
        contract=strict_contract().model_dump(),suite_task_ids=suite['task_ids'],target_indices=suite['target_indices'],epochs=2,seed=42,lora=dict(rank=16,alpha=32.,last_blocks=2))
    write_json(tmp_path/'run'/'recipe.json',recipe);write_json(tmp_path/'run'/'checkpoint'/'summary.json',{})
    anchor=seal(tmp_path/'run'/'checkpoint');write_json(tmp_path/'run'/'summary.json',dict(checkpoint_manifest_sha256=anchor,completed_steps=6,schedule_complete=True));seal(tmp_path/'run')
    sha=hashlib.sha256((tmp_path/'run'/'recipe.json').read_bytes()).hexdigest()
    r=dict(recipe=dict(schedule=epoch_schedule(suite,2),steps=6,lora=recipe['lora']))
    monkeypatch.setattr(adapter,'receipt',lambda *a:r)
    assert validate_training(tmp_path/'run',sha,suite,'a'*64,'b'*64,'c'*64)[1]==anchor
    r['recipe']['schedule']=r['recipe']['schedule'][:-1]+[99]
    with pytest.raises(ValueError):validate_training(tmp_path/'run',sha,suite,'a'*64,'b'*64,'c'*64)
    with pytest.raises(ValueError):validate_training(tmp_path/'run','d'*64,suite,'a'*64,'b'*64,'c'*64)

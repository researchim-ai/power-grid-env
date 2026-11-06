import json,os,signal,subprocess,sys,time
from pathlib import Path
import pytest
from tests.test_campaign import plan
from power_grid_env.campaign import CampaignPlan,run_campaign,verify_campaign
from power_grid_env.study_artifacts import read_json,seal,verify


def multiple(count=4):
    p=plan();p.cases=[p.cases[0].model_copy(update={'id':'job-'+str(i)}) for i in range(count)]
    return p


def test_parallel_memory_admission_and_sequential_physics_match(tmp_path):
    p=multiple();a=run_campaign(p.model_dump(mode='json'),tmp_path/'serial')
    p.maximum_workers=3;p.memory_budget_mb=8192
    b=run_campaign(p.model_dump(mode='json'),tmp_path/'parallel');assert a['passed'] and b['passed']
    assert b['scheduler']['peak_active_workers']==2 and b['scheduler']['peak_reserved_memory_mb']==8192
    assert [r['id'] for r in b['cases']]==[c.id for c in p.cases]
    for c in p.cases:
        first=read_json(tmp_path/'serial/attempts'/c.id/'result.json');second=read_json(tmp_path/'parallel/attempts'/c.id/'result.json')
        for key in ('time_s','channels','event_log','engineering'):assert first[key]==second[key]
    verify_campaign(tmp_path/'parallel')


def test_inadmissible_memory_plan_is_rejected_before_output(tmp_path):
    p=plan().model_dump(mode='json');p['memory_budget_mb']=1024
    with pytest.raises(ValueError,match='memory reservation'):run_campaign(p,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_parallel_stop_on_failure_finishes_inflight_and_defers_pending(tmp_path):
    p=multiple(3);p.maximum_workers=2;p.stop_on_failure=True
    p.cases[0].worker_wall_s=.001
    r=run_campaign(p.model_dump(mode='json'),tmp_path/'run')
    assert [row['status'] for row in r['cases']]==['worker_timeout','passed','not_run_stop_on_failure']
    assert not r['passed'];verify_campaign(tmp_path/'run')


def test_crash_recovery_rejects_forged_passed_flag(tmp_path):
    p=plan();p.cases[0].rules[0].value='impossible'
    run_campaign(p.model_dump(mode='json'),tmp_path/'old')
    attempt=tmp_path/'old/attempts/short';row=read_json(attempt/'execution.json');row['passed']=True
    (attempt/'execution.json').write_text(json.dumps(row));(attempt/'manifest.json').unlink();seal(attempt)
    with pytest.raises(ValueError,match='acceptance differs'):run_campaign(p.model_dump(mode='json'),tmp_path/'new',recover_from=tmp_path/'old')
    assert not (tmp_path/'new').exists()


def test_process_exit_race_is_reaped(monkeypatch):
    from power_grid_env.campaign_executor import stop_worker
    class AlreadyGone:
        pid=12345
        waited=False
        def poll(self):return None
        def wait(self):self.waited=True;return 0
    def vanished(*args):raise ProcessLookupError()
    monkeypatch.setattr(os,'killpg',vanished);process=AlreadyGone()
    assert stop_worker(process)==0 and process.waited


def test_launcher_failure_still_has_a_verified_result(tmp_path,monkeypatch):
    def failure(*args,**kwargs):raise OSError('injected process allocation failure')
    monkeypatch.setattr(subprocess,'Popen',failure)
    r=run_campaign(plan().model_dump(mode='json'),tmp_path/'failed')
    assert not r['passed'] and r['cases'][0]['status']=='worker_start_error'
    verify_campaign(tmp_path/'failed')


def test_extra_attempt_and_nonboolean_accounting_are_rejected(tmp_path):
    run_campaign(plan().model_dump(mode='json'),tmp_path/'old')
    (tmp_path/'old/attempts/unexpected').mkdir();(tmp_path/'old/manifest.json').unlink();seal(tmp_path/'old')
    with pytest.raises(ValueError,match='unexpected'):verify_campaign(tmp_path/'old')
    (tmp_path/'old/attempts/unexpected').rmdir()
    d=read_json(tmp_path/'old/summary.json');d['passed']=1;(tmp_path/'old/summary.json').write_text(json.dumps(d));(tmp_path/'old/manifest.json').unlink();seal(tmp_path/'old')
    with pytest.raises(ValueError,match='boolean'):verify_campaign(tmp_path/'old')


def test_parallel_cancel_reaps_all_active_groups(tmp_path):
    from benchmarks.protected_motor_cases import case
    p=multiple(3);p.maximum_workers=2
    for c in p.cases:c.study=case('aalto-saturated-fan__restart').model_dump(mode='json')
    document=tmp_path/'plan.json';document.write_text(p.model_dump_json());cancel=tmp_path/'cancel'
    process=subprocess.Popen([sys.executable,'-m','power_grid_env.cli','campaign','run','--plan',str(document),'--out',str(tmp_path/'run'),'--cancel-file',str(cancel)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        while len(list((tmp_path/'run').glob('attempts/*/launch.json')))<2:
            assert process.poll() is None and time.monotonic()<deadline
            time.sleep(.02)
        cancel.touch();_,error=process.communicate(timeout=15);assert process.returncode==2,error
        result=read_json(tmp_path/'run/summary.json')
        assert [r['status'] for r in result['cases']]==['cancelled','cancelled','not_run_cancelled']
        verify_campaign(tmp_path/'run')
        for f in (tmp_path/'run').glob('attempts/*/launch.json'):
            with pytest.raises(ProcessLookupError):os.kill(read_json(f)['process_id'],0)
    finally:
        if process.poll() is None:
            process.kill();process.wait()
            for f in (tmp_path/'run').glob('attempts/*/launch.json'):
                try:os.killpg(read_json(f)['process_id'],signal.SIGKILL)
                except ProcessLookupError:pass


def test_atomic_seal_preserves_first_manifest_under_concurrent_publishers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root=tmp_path/'artifact';root.mkdir();(root/'input.json').write_text('{}')
    def publish():
        try:return seal(root)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:publish(),range(4)))
    assert sum(r is not None for r in results)==1;verify(root)
    assert not list(tmp_path.glob('.pge-manifest-*'))


def test_eight_actual_workers_fit_declared_reservations(tmp_path):
    p=multiple(8);p.maximum_workers=8;p.memory_budget_mb=8192
    for case in p.cases:case.memory_limit_mb=1024
    r=run_campaign(p.model_dump(mode='json'),tmp_path/'eight')
    assert r['passed'] and r['scheduler']['peak_active_workers']==8
    assert r['scheduler']['peak_reserved_memory_mb']==8192;verify_campaign(tmp_path/'eight')


def test_scheduler_accounting_cannot_be_resealed_with_false_peaks(tmp_path):
    run_campaign(plan().model_dump(mode='json'),tmp_path/'run')
    d=read_json(tmp_path/'run/summary.json');d['scheduler']['peak_active_workers']=0
    (tmp_path/'run/summary.json').write_text(json.dumps(d));(tmp_path/'run/manifest.json').unlink();seal(tmp_path/'run')
    with pytest.raises(ValueError,match='peak differs'):verify_campaign(tmp_path/'run')


def test_publish_failure_leaves_no_manifest_or_temporary_artifacts(tmp_path,monkeypatch):
    root=tmp_path/'artifact';root.mkdir();(root/'input').write_text('retained')
    def fail(*args):raise OSError('injected link publication failure')
    with monkeypatch.context() as m:
        m.setattr(os,'link',fail)
        with pytest.raises(OSError,match='publication'):seal(root)
    assert not (root/'manifest.json').exists() and not list(tmp_path.glob('.pge-manifest-*'))
    seal(root);verify(root)


def test_post_fork_supervisor_failure_reaps_the_new_process(tmp_path,monkeypatch):
    import power_grid_env.campaign_executor as executor
    original_write=executor.write_json;original_popen=subprocess.Popen;children=[]
    def record(*args,**kwargs):
        process=original_popen(*args,**kwargs);children.append(process);return process
    def fail(path,data):
        if Path(path).name=='launch.json':raise OSError('injected post-fork write failure')
        return original_write(path,data)
    monkeypatch.setattr(subprocess,'Popen',record);monkeypatch.setattr(executor,'write_json',fail)
    with pytest.raises(OSError,match='post-fork'):run_campaign(plan().model_dump(mode='json'),tmp_path/'run')
    assert children and all(child.poll() is not None for child in children)
    assert not (tmp_path/'run/manifest.json').exists()


def test_continuation_parent_mutation_is_not_silently_relabelled(tmp_path,monkeypatch):
    import shutil
    parent=tmp_path/'old';run_campaign(plan().model_dump(mode='json'),parent)
    original=shutil.copytree
    def mutate(source,destination,*args,**kwargs):
        result=original(source,destination,*args,**kwargs)
        if Path(source)==parent/'attempts/short':
            (parent/'report.html').write_text('changed report');(parent/'manifest.json').unlink();seal(parent)
        return result
    monkeypatch.setattr(shutil,'copytree',mutate)
    with pytest.raises(ValueError,match='parent changed'):run_campaign(plan().model_dump(mode='json'),tmp_path/'new',resume_from=parent)
    assert not (tmp_path/'new/manifest.json').exists();verify_campaign(parent)


@pytest.mark.parametrize('filename,value',[('summary.json',[]),('scheduler.json',{}),('manifest.json',[])])
def test_malformed_artifact_shapes_are_value_errors(tmp_path,filename,value):
    run_campaign(plan().model_dump(mode='json'),tmp_path/'run')
    (tmp_path/'run'/filename).write_text(json.dumps(value))
    if filename!='manifest.json':
        (tmp_path/'run/manifest.json').unlink();seal(tmp_path/'run')
    with pytest.raises(ValueError):verify_campaign(tmp_path/'run')


def test_output_poll_tolerates_atomic_temporary_rename(tmp_path,monkeypatch):
    from power_grid_env.campaign_executor import output_exceeded
    temporary=tmp_path/'result.json.tmp';temporary.write_text('intermediate')
    original=Path.stat
    def vanished(path,*args,**kwargs):
        if path==temporary:raise FileNotFoundError('atomic rename completed')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',vanished)
    assert not output_exceeded(tmp_path)

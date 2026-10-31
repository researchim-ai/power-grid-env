"""The actual native RMS worker is bounded, reaped and recoverable."""
import json
import numpy as np
from test_canonical_rms import example
from power_grid_env.study_spec import run_study
from power_grid_env.rms_process import run_isolated_rms


def test_isolated_result_matches_direct_and_cli(tmp_path):
    s=example()
    direct=run_study(s);remote=run_study(s,isolate=True,wall_timeout_s=30.)
    assert direct['completed'] and remote['completed'],remote
    assert remote['channels']['g.delta']==direct['channels']['g.delta']
    assert remote['execution']['mode']=='isolated_process'
    assert remote['execution']['os_sandbox'] is False
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(s.model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'result.json'),'--isolate','--wall-timeout','30'])==0
    assert json.loads((tmp_path/'result.json').read_text())['completed']


def test_deadline_and_cancel_reap_worker_and_next_run_recovers(monkeypatch):
    import power_grid_env.rms_process as worker
    original=worker.subprocess.Popen;processes=[]
    def tracked(*args,**kwargs):
        process=original(*args,**kwargs);processes.append(process);return process
    monkeypatch.setattr(worker.subprocess,'Popen',tracked)
    s=example()
    timeout=run_study(s,wall_timeout_s=.0001)
    assert timeout['status']=='timeout' and not timeout['completed']
    assert processes and processes[-1].poll() is not None
    calls=0
    def cancelled():
        nonlocal calls
        calls+=1
        return calls>=3
    stopped=run_study(s,isolate=True,cancelled=cancelled)
    assert stopped['status']=='cancelled'
    assert processes[-1].poll() is not None
    before=len(processes)
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    assert len(processes)==before
    assert run_study(s,isolate=True,wall_timeout_s=30.)['completed']


def test_worker_output_budget_is_enforced():
    report=run_isolated_rms(example(),max_result_bytes=10,timeout_s=30.)
    assert report['status']=='worker_output_limit'
    assert not report['completed']


def test_atomic_result_publication_during_budget_probe_is_not_an_error(tmp_path,monkeypatch):
    from pathlib import Path
    from power_grid_env.rms_process import _output_over_budget
    temporary=tmp_path/'result.json.tmp';final=tmp_path/'result.json'
    temporary.write_bytes(b'{}')
    original=Path.stat
    renamed=False
    def publish_during_probe(path,*args,**kwargs):
        nonlocal renamed
        if path==temporary and not renamed:
            renamed=True;temporary.replace(final)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',publish_during_probe)
    assert not _output_over_budget(tmp_path,100,100)
    assert renamed and final.read_bytes()==b'{}'
    assert not _output_over_budget(tmp_path,100,100)


def test_atomic_publication_keeps_final_result_size_limit(tmp_path,monkeypatch):
    from pathlib import Path
    from power_grid_env.rms_process import _output_over_budget
    temporary=tmp_path/'result.json.tmp';final=tmp_path/'result.json'
    temporary.write_bytes(b'x'*101)
    original=Path.stat
    def publish_during_probe(path,*args,**kwargs):
        if path==temporary and not final.exists():temporary.replace(final)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',publish_during_probe)
    # Rename after the final-path probe can defer detection to the next scan;
    # run_isolated_rms scans again after process exit, before loading a result.
    assert not _output_over_budget(tmp_path,100,100)
    assert _output_over_budget(tmp_path,100,100)

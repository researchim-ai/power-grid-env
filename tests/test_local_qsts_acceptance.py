from pathlib import Path
import json
from benchmarks import local_qsts_acceptance as acceptance


def mock_inputs(tmp_path,monkeypatch):
    raw=tmp_path/'raw';feeder=raw/'feeder';feeder.mkdir(parents=True)
    (feeder/'Master.dss').write_text('! local fixture')
    (feeder/'LoadShapes.dss').write_text('new loadshape.a file=../profile.csv')
    (raw/'profile.csv').write_text('1\n2\n')
    monkeypatch.setattr(acceptance.reference,'FEEDER',feeder)
    monkeypatch.setattr(acceptance.reference,'RAW',raw)
    monkeypatch.setattr(acceptance,'source_hashes',lambda repo:{'fixture':'fixed'})
    return raw


def test_missing_data_never_passes(tmp_path,monkeypatch):
    monkeypatch.setattr(acceptance.reference,'FEEDER',tmp_path/'absent')
    result=acceptance.qualify(tmp_path/'out')
    assert not result['passed'] and result['errors'] and not result['cases']


def test_cached_windows_and_gates(tmp_path,monkeypatch):
    mock_inputs(tmp_path,monkeypatch);calls=[]
    def native(out,*,steps):
        calls.append(steps)
        return dict(verdict='pass',replay=dict(verdict='pass'),window=dict(steps=steps),max_voltage_error_pu=0.)
    monkeypatch.setattr(acceptance.reference,'run',native)
    monkeypatch.setattr(acceptance.reference,'engine_window',lambda steps:dict(verdict='pass',steps=steps,max_voltage_error_pu=0.))
    result=acceptance.qualify(tmp_path/'out')
    assert result['passed'] and calls==[96,672]
    assert not result['second_external_feeder_qualified'] and not result['independent_solver_agreement']
    assert len(json.loads((tmp_path/'out/raw-inputs.json').read_text()))==3


def test_resume_failure_and_raw_mutation_fail_acceptance(tmp_path,monkeypatch):
    raw=mock_inputs(tmp_path,monkeypatch)
    def native(out,*,steps):
        (raw/'profile.csv').write_text('3\n')
        return dict(verdict='pass',replay=dict(verdict='fail'),window=dict(steps=steps))
    monkeypatch.setattr(acceptance.reference,'run',native)
    monkeypatch.setattr(acceptance.reference,'engine_window',lambda steps:dict(verdict='pass',steps=steps))
    result=acceptance.qualify(tmp_path/'out')
    assert not result['passed'] and 'raw inputs changed during acceptance' in result['errors']
    assert all(not row['passed'] for row in result['cases'])


def test_profile_outside_dataset_is_rejected(tmp_path,monkeypatch):
    raw=mock_inputs(tmp_path,monkeypatch)
    (raw/'feeder/LoadShapes.dss').write_text('new loadshape.a file=../../outside.csv')
    inventory,errors=acceptance.inputs()
    assert 'input escapes raw dataset root' in errors


def test_explicit_second_feeder_inputs_do_not_reuse_first_profile_paths(tmp_path,monkeypatch):
    raw=mock_inputs(tmp_path,monkeypatch)
    second=raw/'second';second.mkdir();(second/'Master.dss').write_text('! own feeder')
    (second/'LoadShapes.dss').write_text('new loadshape.b file=../own.csv');(raw/'own.csv').write_text('9\n8\n')
    files,errors=acceptance.inputs(second)
    assert not errors and 'own.csv' in files and 'profile.csv' not in files


def test_two_feeder_missing_second_cannot_be_qualified(tmp_path,monkeypatch):
    from benchmarks import two_feeder_qsts
    raw=mock_inputs(tmp_path,monkeypatch)
    result=two_feeder_qsts.run(tmp_path/'two')
    assert not result['passed'] and not result['second_external_feeder_qualified']

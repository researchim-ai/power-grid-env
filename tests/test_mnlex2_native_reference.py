"""Independent compiler, integrity checks and complete limiter reset waveforms."""
from pathlib import Path
import json,os
import numpy as np
import pytest
from benchmarks.mnlex2_native_reference import example,NAMES,primary_reference,native_reference,compare
from benchmarks import modelica_reference_setup as setup


@pytest.mark.parametrize('name',NAMES)
def test_component_output_and_raw_state_have_distinct_contracts(name):
    case=example(name);r=primary_reference(case);p=case['passport']
    assert r['completed']
    assert min(r['channels']['output_pu'])>=0 and max(r['channels']['output_pu'])<=p['MEL_MAX']
    assert min(r['channels']['raw_lag_state_pu'])<0
    if name not in ('hold','inactive_equilibrium'):assert any(e['kind']=='uel_lag_reset' for e in r['event_log'])
    if name=='saturation_recovery':assert max(r['channels']['raw_lag_state_pu'])>p['MEL_MAX']


@pytest.mark.parametrize('name',['activation_recovery','saturation_recovery','fast_feedback','inactive_equilibrium','linear_recovery'])
def test_original_modelica_equations_match_full_saved_waveforms(name,tmp_path):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional hash-pinned OpenModelica runtime not configured')
    setup.verify(root);case=example(name)
    native=native_reference(case,tmp_path/'native',root,1e-11)
    primary=primary_reference(case)
    assert compare(primary,native)['passed']
    assert native['original_OpenIPSL_source']


def test_changed_reference_archive_fails_before_compiler(tmp_path):
    lock=json.loads(setup.LOCK.read_text());first=next(iter(lock['packages'].values()));(tmp_path/'packages').mkdir()
    (tmp_path/'packages'/first['filename']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed reference archive'):setup.verify(tmp_path)


def test_missing_reference_does_not_silently_download_or_substitute_engine(tmp_path):
    with pytest.raises(ValueError,match='missing optional reference archive'):setup.prepare(tmp_path)


def test_missing_native_grid_point_cannot_be_hidden_by_interpolation():
    primary=dict(time_s=[0.,.5,1.],channels={k:[0.,0.,0.] for k in ['output_pu','raw_lag_state_pu','feedback_pu','circle_error_pu2']})
    native=dict(time_s=[0.,1.],channels={k:[0.,0.] for k in primary['channels']})
    with pytest.raises(ValueError,match='grid sample missing'):compare(primary,native)


def test_post_event_grid_comparison_checks_raw_reset_instead_of_clipped_output_only():
    keys=['output_pu','raw_lag_state_pu','feedback_pu','circle_error_pu2']
    primary=dict(time_s=[0.,1.],channels={k:[0.,0.] for k in keys})
    native=dict(time_s=[0.,1.,1.],channels={k:[0.,1.,0.] for k in keys})
    assert compare(primary,native)['passed']
    native['channels']['raw_lag_state_pu'][-1]=.01
    assert not compare(primary,native)['passed']

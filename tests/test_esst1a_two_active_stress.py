"""Unqualified two-active-UEL counterexample, never an expected positive result."""
from pathlib import Path
import os,json
import pytest
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.generator_esst1a_joint_validation import compare,expected_rejection

CASE=Path(__file__).resolve().parents[1]/'cases/studies/generator_esst1a_two_active_limiters_stress.json'

@pytest.fixture(scope='module')
def primary():
    s=StudySpec.model_validate_json(CASE.read_text());a=run_study(s)
    assert a['completed'],a
    assert len(s.coupled.esst1a_exciters)==len(s.coupled.underexcitation_limiters)==2
    assert {p.generator_id for p in s.coupled.underexcitation_limiters}=={'g','g2'}
    for gid in ['g','g2']:assert max(a['channels'][gid+'.UEL.output_pu'])>.01
    assert a['maximum_kcl_residual_pu']<1e-8 and a['maximum_power_balance_residual_pu']<1e-8
    return s,a


def test_stress_case_is_replayable_and_both_limiters_actually_activate(primary):
    s,a=primary
    assert StudySpec.model_validate_json(s.model_dump_json()).model_dump()==s.model_dump()
    assert s.id.endswith('stress')


@pytest.mark.parametrize('tolerance',[1e-11,1e-12])
def test_two_active_native_limiters_remain_explicitly_unqualified(primary,tolerance,tmp_path):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    from benchmarks.esst1a_network import run
    s,a=primary;b=run(s,tmp_path/'native',Path(root),tolerance,native_uels=True,uel_event_policy='broadcast',uel_root_policy='compound_and_atoms');c=compare(s,a,b)
    assert b['completed'] and b['native_MNLEX2_in_feedback'] and b['native_ESST1A_in_feedback']
    assert c['reset_audit_complete'] and not c['passed']
    assert not expected_rejection('two_active_limiters_stress',c)
    assert any(f.get('event_time_error_s',0.)>.001 for f in c['reset_failures'])
    assert any(c['errors'][k]>c['limits'][k] for k in c['errors'])
    json.dumps(c,allow_nan=False)

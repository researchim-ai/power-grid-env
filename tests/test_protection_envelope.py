import copy
from pathlib import Path
import pytest
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans,run_envelope,verify_envelope
from power_grid_env.study_artifacts import read_json,encoded,seal
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def envelope():return read_json(ROOT/'cases/inverse-protection/envelope.json')


def test_complete_cartesian_mapping_and_immutability(envelope):
    before=copy.deepcopy(envelope);variants=list(effective_plans(envelope))
    assert envelope==before and len(variants)==17
    assert len({tuple(r['signs']) for _,_,r in variants if r['signs'] is not None})==16
    for name,p,r in variants[1:]:
        assert p.primary.pickup_tolerance==0 and p.backup.time_tolerance==0
        for (stage,key,tol),sign in zip([('primary','pickup_a','pickup_tolerance'),('primary','time_multiplier','time_tolerance'),('backup','pickup_a','pickup_tolerance'),('backup','time_multiplier','time_tolerance')],r['signs']):
            assert getattr(getattr(p,stage),key)==pytest.approx(envelope['base'][stage][key]*(1+sign*envelope['base'][stage][tol]))
        assert p.equipment.model_dump(mode='json')==variants[0][1].equipment.model_dump(mode='json')

@pytest.mark.parametrize('change',['policy','zero','unknown','budget'])
def test_invalid_envelope(envelope,change):
    if change=='policy':envelope['base']['clock_policy']='definite_analytic'
    if change=='zero':
        for s in ['primary','backup']:envelope['base'][s].update(pickup_tolerance=0,time_tolerance=0)
    if change=='unknown':envelope['extra']=1
    if change=='budget':envelope['total_timeout_s']=float('nan')
    with pytest.raises(ValueError):ProtectionEnvelope.model_validate(envelope)


def test_cancelled_envelope_has_no_positive_label(envelope,tmp_path):
    r=run_envelope(envelope,tmp_path/'cancelled',cancelled=lambda:True)
    assert not r['completed'] and not r['passes_all_enumerated_corners'] and r['reward'] is None
    assert not list((tmp_path/'cancelled').glob('corner-*/plan.json'))


def test_orchestration_does_not_hide_negative_corner(envelope,tmp_path,monkeypatch):
    # Scheduling-only fake: physical runs are qualified separately by the
    # actual 17-study acceptance matrix, not by this unit test.
    import power_grid_env.protection_envelope as m
    def fake(plan,out,**kwargs):
        Path(out).mkdir()
        return dict(completed=True,numerically_qualified=True,passes_declared_profile=not plan.id.endswith('1111'))
    monkeypatch.setattr(m,'run_closed_loop',fake)
    r=run_envelope(envelope,tmp_path/'matrix')
    assert r['completed'] and r['numerically_qualified'] and not r['passes_all_enumerated_corners']
    assert len(r['rows'])==17 and not r['rows'][-1]['passes_declared_profile']

import copy
from pathlib import Path

import pytest
from power_grid_env.motor_robustness import MotorRobustnessPlan,expand_plan,summarize,run_robustness,verify_robustness
from power_grid_env.study_artifacts import read_json,encoded,seal,export_zip,extract_zip

ROOT=Path(__file__).resolve().parents[1]


def example():
    base=read_json(ROOT/'cases/motor-engineering/multibus-generator-restart.json')['cases'][1]
    return dict(id='robust',base=base,bounds_provenance='Authored synthetic stress factors; no measured parameter distribution',
        axes=[dict(id='inertia',parameter='inertia_s',equipment_id='m',factors=[1.,1.25]),
              dict(id='load',parameter='mechanical_torque',equipment_id='m',factors=[1.,1.2])])


def test_cartesian_design_preserves_nominal_and_mutates_only_frozen_copies():
    raw=example();before=copy.deepcopy(raw);plan,expanded,points=expand_plan(raw)
    assert raw==before and len(points)==4 and points[0]['nominal']
    motors=[c.study.coupled.motors[0] for c in expanded.cases]
    assert [m.inertia_s for m in motors]==pytest.approx([.3,.3,.375,.375])
    assert all(c.expectation=='within_limits' for c in expanded.cases)
    assert len({c.study.id for c in expanded.cases})==4
    assert all(c.study.coupled.motors[1]==expanded.cases[0].study.coupled.motors[1] for c in expanded.cases)


def test_one_at_a_time_never_claims_corner_coverage():
    raw=example();raw['design']='one_at_a_time'
    _,_,points=expand_plan(raw)
    assert len(points)==3 and not any(p['factors']==dict(inertia=1.25,load=1.2) for p in points)


@pytest.mark.parametrize('defect',['duplicate_id','duplicate_target','missing_nominal','unknown_motor','too_many','blank_source','negative_factor','unknown_parameter','double_cage','negative_expectation'])
def test_invalid_design_rejected_before_any_execution(defect):
    d=example()
    if defect=='duplicate_id':d['axes'][1]['id']='inertia'
    elif defect=='duplicate_target':d['axes'][1].update(parameter='inertia_s',equipment_id='m')
    elif defect=='missing_nominal':d['axes'][0]['factors']=[.9,1.2]
    elif defect=='unknown_motor':d['axes'][0]['equipment_id']='missing'
    elif defect=='too_many':d['axes'][0]['factors']=[.8,.9,1.,1.1,1.2]
    elif defect=='blank_source':d['bounds_provenance']=' '
    elif defect=='negative_factor':d['axes'][0]['factors']=[1.,-1]
    elif defect=='unknown_parameter':d['axes'][0]['parameter']='arbitrary'
    elif defect=='double_cage':d['axes'][0]['parameter']='rotor2_r_pu'
    else:d['base']['expectation']='violates_limits'
    with pytest.raises(ValueError):expand_plan(d)


@pytest.mark.parametrize('qualified,failures,status',[(True,[], 'all_sampled_points_within_limits'),(True,[1],'violations_found'),(False,[1],'inconclusive')])
def test_summary_distinguishes_failure_from_unknown(qualified,failures,status):
    p,s,points=expand_plan(example())
    rows=[dict(id=point['id'],numerically_qualified=qualified or i!=2,engineering_acceptable=i not in failures) for i,point in enumerate(points)]
    r=summarize(p,points,dict(completed=qualified,producer_sha256='a'*64,cases=rows))
    assert r['status']==status and not r['continuous_range_proven']
    assert r['probability_of_success'] is None
    assert bool(r['violating_point_ids'])==bool(failures)


def test_missing_or_reordered_point_cannot_pass():
    p,s,points=expand_plan(example())
    with pytest.raises(ValueError,match='coverage'):summarize(p,points,dict(cases=[]))


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    d=example();d['axes']=d['axes'][:1];s=d['base']['study']['coupled'];s['duration_s']=.15;s['events']=[]
    for window in d['base']['voltage_windows']:window.update(from_s=0,to_s=.15,minimum_voltage_pu=0,maximum_voltage_pu=2)
    out=tmp_path_factory.mktemp('robustness')/'packet'
    result=run_robustness(d,out)
    assert result['status']=='violations_found'
    return out


def test_real_three_grid_evidence_and_zip_roundtrip(packet,tmp_path):
    result=verify_robustness(packet)
    assert result['verified'] and result['all_points_qualified'] and not result['all_sampled_points_within_limits']
    export_zip(packet,tmp_path/'robust.zip');extract_zip(tmp_path/'robust.zip',tmp_path/'unpacked')
    assert verify_robustness(tmp_path/'unpacked')==result


def test_resealed_summary_cannot_turn_failure_into_success(packet,tmp_path):
    import shutil
    out=tmp_path/'forged';shutil.copytree(packet,out)
    summary=read_json(out/'summary.json');summary['all_sampled_points_within_limits']=True
    (out/'summary.json').write_bytes(encoded(summary));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='summary differs'):verify_robustness(out)


def test_cli_and_schema_export(packet,tmp_path,capsys):
    from power_grid_env.cli import main
    assert main(['verify-motor-robustness',str(packet)])==0
    capsys.readouterr()
    assert main(['schemas','--out',str(tmp_path/'schemas')])==0
    assert read_json(tmp_path/'schemas/motor-robustness.schema.json')['title']=='MotorRobustnessPlan'


def test_cancelled_design_is_inconclusive_and_cannot_be_verified_as_complete(tmp_path):
    out=tmp_path/'cancelled'
    result=run_robustness(example(),out,cancelled=lambda:True)
    assert result['status']=='inconclusive'
    assert not result['all_points_qualified'] and len(result['unqualified_point_ids'])==4
    assert result['nominal_within_limits'] is None
    with pytest.raises(ValueError,match='complete current-producer'):verify_robustness(out)


def test_resealed_factor_edit_cannot_reuse_a_different_study(packet,tmp_path):
    import shutil
    out=tmp_path/'edited-design';shutil.copytree(packet,out)
    plan=read_json(out/'input.json');plan['axes'][0]['factors']=[1.,1.5]
    (out/'input.json').write_bytes(encoded(plan));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='study differs'):verify_robustness(out)

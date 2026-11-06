import json
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.phase_research import PhaseStudy,SearchProtocol,run_phase_study,run_search,fit_surrogate,predict_surrogate,train_from_search
from power_grid_env.study_artifacts import verify,read_json
from test_phase_network import rlc


def protocol():
    return dict(id='authored-grid',study=dict(id='rlc',mode='harmonic',case=rlc().model_dump(mode='json'),orders=[1]),
                axes=[dict(id='load',parameter='resistance_ohm',equipment_id='load-r',values=[16.,18.,20.,22.,24.]),
                      dict(id='line',parameter='inductor_resistance_ohm',equipment_id='line',values=[.8,.9,1.,1.1,1.2])],
                objective=dict(kind='node_rms_v',equipment_id='load',sense='max',threshold=300.))


def test_physical_grid_resume_critical_replay_and_holdout_model(tmp_path):
    p=protocol();part=run_search(p,tmp_path/'part',max_points=7);assert part['status']=='paused' and not part['completed']
    before=verify(tmp_path/'part');resumed=run_search(p,tmp_path/'resumed',resume_from=tmp_path/'part');full=run_search(p,tmp_path/'full')
    assert full['completed'] and full['conclusive'] and full['total_points']==25 and full['critical_case_physically_replayed']
    assert read_json(tmp_path/'full/points.json')==read_json(tmp_path/'resumed/points.json')
    assert verify(tmp_path/'part')==before
    model=train_from_search(tmp_path/'full',tmp_path/'model',maximum_holdout_error=1.)
    assert model['accepted']
    prediction=predict_surrogate(model,[20.,1.]);assert prediction['requires_physical_verification']
    assert abs(prediction['prediction']-next(r['objective'] for r in read_json(tmp_path/'full/points.json') if r['values']==[20.,1.]))<1.
    with pytest.raises(ValueError,match='convex hull'):predict_surrogate(model,[40.,1.])
    with pytest.raises(ValueError,match='complete'):train_from_search(tmp_path/'part',tmp_path/'invalid-model',maximum_holdout_error=1.)


def test_holdout_labels_do_not_leak_into_fitted_coefficients():
    x=np.array([[i,j] for i in np.linspace(-1,1,5) for j in np.linspace(-2,2,5)])
    y=3+2*x[:,0]-4*x[:,1]+.5*x[:,0]**2
    first=fit_surrogate(x,y,maximum_holdout_error=1e-8)
    changed=y.copy();changed[first['holdout_indices']]+=100
    second=fit_surrogate(x,changed,maximum_holdout_error=1.)
    assert first['coefficients']==second['coefficients'] and first['output_offset']==second['output_offset']
    assert first['accepted'] and not second['accepted']
    with pytest.raises(ValueError,match='not accepted'):predict_surrogate(second,[0.,0.])


def test_degenerate_model_and_missing_observations_are_rejected():
    with pytest.raises(ValueError):fit_surrogate([[0],[1]],[0,1],maximum_holdout_error=1.)
    with pytest.raises(ValueError):fit_surrogate([[1,1]]*20,list(range(20)),maximum_holdout_error=1.)
    with pytest.raises(ValueError):fit_surrogate([[0],[1],[2],[3],[4],[5]],[1,2,float('nan'),4,5,6],maximum_holdout_error=1.)


def test_cancel_changed_inputs_and_tamper_do_not_become_success(tmp_path):
    p=protocol();cancel=tmp_path/'cancel';cancel.touch()
    stopped=run_search(p,tmp_path/'stopped',cancel_file=cancel);assert stopped['status']=='cancelled' and not stopped['completed']
    altered=protocol();altered['id']='another'
    with pytest.raises(ValueError,match='differs'):run_search(altered,tmp_path/'wrong',resume_from=tmp_path/'stopped')
    (tmp_path/'stopped/points.json').write_text('[] ')
    with pytest.raises(ValueError,match='changed'):run_search(p,tmp_path/'corrupt',resume_from=tmp_path/'stopped')


def test_new_phase_artifact_is_sealed_and_static_nonlinear_misuse_rejected(tmp_path):
    study=dict(id='linear',case=rlc().model_dump(mode='json'),mode='harmonic',orders=[1,3])
    result=run_phase_study(study,tmp_path/'study');assert result['completed'];verify(tmp_path/'study')
    with pytest.raises(ValueError,match='new'):run_phase_study(study,tmp_path/'study')
    study['case']=rlc(saturated=True).model_dump(mode='json')
    with pytest.raises(ValueError,match='linear'):run_phase_study(study,tmp_path/'nonlinear')


def test_grid_failure_remains_inconclusive_and_is_not_trainable(tmp_path,monkeypatch):
    from power_grid_env import phase_research
    original=phase_research.evaluate
    def fail(study):
        if study.case.resistors[0].resistance_ohm==16.:raise RuntimeError('authored solver failure')
        return original(study)
    monkeypatch.setattr(phase_research,'evaluate',fail)
    result=run_search(protocol(),tmp_path/'grid');assert result['completed'] and not result['conclusive'] and result['failed_points']==5
    with pytest.raises(ValueError,match='conclusive'):train_from_search(tmp_path/'grid',tmp_path/'model',maximum_holdout_error=1.)


def test_query_is_physically_verified_by_default_and_screening_is_explicit(tmp_path):
    from power_grid_env.phase_research import query_model
    run_search(protocol(),tmp_path/'grid');train_from_search(tmp_path/'grid',tmp_path/'model',maximum_holdout_error=1.)
    verified=query_model(tmp_path/'model',[20.,1.],tmp_path/'verified')
    assert verified['physically_verified'] and verified['accepted'] and not verified['requires_physical_verification']
    screen=query_model(tmp_path/'model',[20.,1.],tmp_path/'screen',screen_only=True)
    assert not screen['physically_verified'] and not screen['accepted'] and screen['requires_physical_verification']


def test_phase_cli_complete_workflow_and_bad_inputs(tmp_path,capsys):
    from power_grid_env.cli import main
    input_=tmp_path/'protocol.json';input_.write_text(json.dumps(protocol()))
    assert main(['phase-study','search','--protocol',str(input_),'--out',str(tmp_path/'part'),'--max-points','4'])==2
    assert main(['phase-study','search','--protocol',str(input_),'--out',str(tmp_path/'full'),'--resume-from',str(tmp_path/'part')])==0
    assert main(['phase-study','train',str(tmp_path/'full'),'--out',str(tmp_path/'model'),'--max-error','1'])==0
    assert main(['phase-study','predict',str(tmp_path/'model'),'--point','[20,1]','--out',str(tmp_path/'verified')])==0
    assert main(['phase-study','predict',str(tmp_path/'model'),'--point','[20,1]','--screen-only','--out',str(tmp_path/'screen')])==2
    assert main(['phase-study','verify',str(tmp_path/'verified')])==0
    assert main(['phase-study','export',str(tmp_path/'verified'),'--out',str(tmp_path/'result.zip')])==0


def test_batch_screening_preserves_pointwise_values_and_domain_guards():
    from power_grid_env.phase_research import predict_surrogate_batch
    x=[[i,j] for i in np.linspace(-1,1,5) for j in np.linspace(-2,2,5)];y=[a*a+2*b for a,b in x]
    model=fit_surrogate(x,y,maximum_holdout_error=1e-8)
    result=predict_surrogate_batch(model,[[0.,0.],[.1,.2]])
    assert result['requires_physical_verification'] and not result['empirical_error_is_guaranteed']
    np.testing.assert_allclose(result['predictions'],[0.,.41],atol=1e-10)
    with pytest.raises(ValueError,match='convex hull'):predict_surrogate_batch(model,[[0.,0.],[100.,0.]])


def test_duplicate_observations_cannot_leak_one_physical_point_into_both_sets():
    x=[[float(i)] for i in range(10)]+[[0.]]
    with pytest.raises(ValueError,match='unique physical'):fit_surrogate(x,[i[0] for i in x],maximum_holdout_error=1.)

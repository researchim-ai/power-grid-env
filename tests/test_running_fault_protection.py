import copy
from pathlib import Path
import pytest
from benchmarks.running_fault_protection_reference import recipes
from power_grid_env.study_artifacts import read_json,encoded,seal
from power_grid_env.running_fault_protection import run,assess,verify_clearance,ClearanceRequirements

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'cases/running-fault-protection/base-revised.json'

@pytest.fixture(scope='module',params=[(False,'radau'),(True,'radau'),(False,'network_dae'),(True,'network_dae')])
def packet(request,tmp_path_factory):
    protected,method=request.param
    for kind,uv,m,grid,study,req in recipes(read_json(BASE)):
        if kind=='primary' and uv==protected and m==method and grid==0:break
    out=tmp_path_factory.mktemp('running-fault')/'packet';result=run(study,req,out)
    return protected,study,req,out,result


def test_physical_isolation_is_not_fault_extinction(packet):
    uv,study,req,out,r=packet
    assert r['passed']==uv
    assert r['checks']['grid_electrically_isolated']
    assert r['checks']['actuator_delay_audit'] and r['checks']['clock_audits']
    assert r['maximum_sampled_fault_current_after_grid_isolation_a']>50
    if uv:
        assert r['fault_extinction_time_s']==pytest.approx(3.09)
        assert r['grid_isolation_time_s']<r['fault_extinction_time_s']
    else:
        assert r['fault_extinction_time_s'] is None
        assert not r['checks']['motors_disconnected']
    assert verify_clearance(out,expected_study=study,expected_requirements=req)['verified']


@pytest.mark.parametrize('defect',['code','network','settings','event_sides','current_length','negative_current','time_reversal','invalid_state'])
def test_bad_trace_rejected(packet,defect):
    _,study,req,out,_=packet;report=read_json(out/'trace.json');fault=study.coupled.faults[0].id
    if defect=='code':report['code_sha256']='0'*64
    elif defect=='network':report['network_sha256']='0'*64
    elif defect=='settings':report['settings']['step_s']*=2
    elif defect=='event_sides':report['sample_side'][report['time_s'].index(3.)]='regular'
    elif defect=='current_length':report['channels'][fault+'.current_a'].pop()
    elif defect=='negative_current':report['channels'][fault+'.current_a'][0]=-1
    elif defect=='time_reversal':report['time_s'][1]=-1
    else:report['channels']['primary-cb.closed'][0]=.5
    with pytest.raises(ValueError):assess(study,report,req)


def test_short_dip_does_not_prove_extinction(packet):
    uv,study,req,out,_=packet;report=read_json(out/'trace.json')
    if uv:return
    fault=study.coupled.faults[0].id
    i=next(i for i,t in enumerate(report['time_s']) if t>3.1)
    report['channels'][fault+'.current_a'][i]=0
    assert assess(study,report,req)['fault_extinction_time_s'] is None


def test_false_breaker_log_or_state_not_enough(packet):
    _,study,req,out,_=packet;report=read_json(out/'trace.json')
    report['channels']['primary-cb.closed']=[1]*len(report['time_s'])
    r=assess(study,report,req)
    assert not r['passed'] and not r['checks']['grid_electrically_isolated']
    assert not r['checks']['opening_event_sides']


def test_fake_actuator_deadline_fails(packet):
    _,study,req,out,_=packet;report=read_json(out/'trace.json')
    for event in report['event_log']:
        if event['kind']=='breaker_open_requested':event['due_s']+=.01
    assert not assess(study,report,req)['checks']['actuator_delay_audit']


def test_owner_and_resealed_result(packet,tmp_path):
    import shutil
    _,study,req,out,_=packet
    other=copy.deepcopy(req);other['extinction_current_a']=1
    with pytest.raises(ValueError,match='owner'):verify_clearance(out,expected_study=study,expected_requirements=other)
    dest=tmp_path/'copy';shutil.copytree(out,dest)
    r=read_json(dest/'result.json');r['passed']=not r['passed']
    (dest/'result.json').write_bytes(encoded(r));(dest/'manifest.json').unlink();seal(dest)
    with pytest.raises(ValueError,match='result'):verify_clearance(dest,expected_study=study,expected_requirements=req)


def test_scheduled_clear_cannot_mask_owner_window(packet):
    _,study,req,out,_=packet;req=copy.deepcopy(req)
    req['maximum_fault_extinction_s']=.29
    with pytest.raises(ValueError,match='masks'):assess(study,read_json(out/'trace.json'),req)


def test_uv_is_only_change_in_paired_recipes():
    by_key={}
    for kind,uv,method,grid,study,req in recipes(read_json(BASE)):
        data=study.model_dump(mode='json')
        for m in data['coupled']['motors']:m['contactor']=None
        by_key.setdefault((kind,method,grid),[]).append((data,req))
    assert len(by_key)==18 and all(v[0]==v[1] for v in by_key.values())


def test_revision_is_explicit_backup_tms_only():
    from benchmarks.running_fault_revision import validate_revision
    original=read_json(ROOT/'cases/running-fault-protection/base-original.json')
    candidate=read_json(BASE)
    assert validate_revision(original,candidate)==dict(original_backup_tms=.07,candidate_backup_tms=.015)
    candidate['primary']['pickup_a']+=1
    with pytest.raises(ValueError,match='only'):validate_revision(original,candidate)

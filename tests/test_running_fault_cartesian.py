import copy
import time
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json,encoded,seal
from power_grid_env.protection_envelope import effective_plans
from benchmarks.running_fault_cartesian import CartesianProtocol,conditions,condition_jobs,execute,verify_job,summary,producer,collect,run_store,verify_store

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def protocol():return read_json(ROOT/'cases/running-fault-cartesian/protocol.json')


def test_complete_cartesian_coverage_and_catalog_derivation(protocol):
    p=CartesianProtocol.model_validate(protocol);all_jobs=[]
    for id,c in conditions(p):
        for name,base,_ in effective_plans(p.envelope):
            jobs=list(condition_jobs(p,base,c));assert len(jobs)==36
            for job in jobs:
                expected=.020265 if c['conductor_temperature_c']==20 else .0258399015
                n=job['study']['network']
                assert n['lines'][0]['r_ohm'][0][0]==pytest.approx(expected)
                assert n['sources'][0]['ll_v']==pytest.approx(400*c['source_voltage_factor'])
                if job['kind']=='fault':assert job['requirements']['maximum_fault_extinction_s']==.15
                else:assert job['case']['study']==job['study']
            all_jobs.extend((id,name,j['id']) for j in jobs)
    assert len(all_jobs)==2448 and len(set(all_jobs))==2448


@pytest.mark.parametrize('field,value',[
 ('conductor_temperatures_c',[90,20]),('conductor_temperatures_c',[20,20]),
 ('conductor_temperatures_c',[19,90]),('conductor_temperatures_c',[20,91]),
 ('source_voltage_factors',[.95,float('nan')]),('source_voltage_factors',[.4,1.05]),
 ('source_voltage_factors',[1.05,.95]),('maximum_studies',2447),('wall_budget_s',0)])
def test_invalid_protocol(protocol,field,value):
    protocol[field]=value
    with pytest.raises(ValueError):CartesianProtocol.model_validate(protocol)


def test_owner_catalog_mismatch(protocol):
    protocol['brief']['installation']['length_m']+=1
    with pytest.raises(ValueError,match='differ'):CartesianProtocol.model_validate(protocol)


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    p=CartesianProtocol.model_validate(read_json(ROOT/'cases/running-fault-cartesian/protocol.json'))
    c=next(c for _,c in conditions(p));base=p.envelope.base
    j=next(j for j in condition_jobs(p,base,c) if j['kind']=='normal' and j['scenario']=='start' and j['method']=='radau' and j['grid']==0)
    root=tmp_path_factory.mktemp('cartesian')/'study';r=execute((j,str(root),False));return j,root,r


def test_saved_job_and_cached_resume(packet,monkeypatch):
    import benchmarks.running_fault_cartesian as module
    job,root,result=packet;assert result['passed']
    monkeypatch.setattr(module,'run_study',lambda *_:pytest.fail('resume must not integrate'))
    assert execute((job,str(root),False))==result


def test_condition_or_criteria_substitution_rejected(packet):
    job,root,_=packet;wrong=copy.deepcopy(job);wrong['condition']['conductor_temperature_c']=90
    with pytest.raises(ValueError,match='condition'):verify_job(wrong,root)
    wrong=copy.deepcopy(job);wrong['case']['motors'][0]['minimum_final_speed_pu']=0
    with pytest.raises(ValueError,match='criteria'):verify_job(wrong,root)


def test_resealed_ct_corruption_not_hidden_by_clock(packet,tmp_path):
    import shutil
    job,root,_=packet;copyroot=tmp_path/'copy';shutil.copytree(root,copyroot)
    r=read_json(copyroot/'trace.json');r['channels']['primary-oc.measurement'][100]+=5
    (copyroot/'trace.json').write_bytes(encoded(r));(copyroot/'manifest.json').unlink();seal(copyroot)
    row=verify_job(job,copyroot);assert not row['passed'] and not row['algebra']['passed']


def test_verification_cannot_integrate_missing_study(packet,tmp_path,monkeypatch):
    import benchmarks.running_fault_cartesian as module
    monkeypatch.setattr(module,'run_study',lambda *_:pytest.fail('verification must not integrate'))
    with pytest.raises(ValueError,match='cannot create'):execute((packet[0],str(tmp_path/'missing'),True))


def test_expired_budget_submits_no_work():
    class Pool:
        def submit(self,*a):pytest.fail('expired budget must not submit')
    with pytest.raises(TimeoutError):collect(Pool(),[object()],time.monotonic()-1)


def test_incomplete_native_trace_preserved_as_negative(packet,tmp_path,monkeypatch):
    import benchmarks.running_fault_cartesian as module
    job,_,_=packet;root=tmp_path/'failure'
    monkeypatch.setattr(module,'run_study',lambda *_:dict(completed=False,solved=False,status='budget_exhausted',reason='test budget'))
    row=execute((job,str(root),False))
    assert not row['passed'] and not row['numerically_complete']
    assert read_json(root/'trace.json')['reason']=='test budget'
    assert not read_json(root/'result.json')['passed']


def test_incomplete_index_and_extra_layout(protocol,tmp_path):
    p=CartesianProtocol.model_validate(protocol)
    assert not summary(p,[],producer())['passed']
    (tmp_path/'extra').write_text('x')
    with pytest.raises(ValueError,match='layout'):verify_store(tmp_path,p)


@pytest.mark.parametrize('workers',[0,17])
def test_worker_budget(protocol,tmp_path,workers):
    with pytest.raises(ValueError,match='workers'):run_store(protocol,tmp_path/'bad',workers=workers)
    assert not (tmp_path/'bad').exists()

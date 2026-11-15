import copy
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json,encoded,seal
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from benchmarks.running_fault_corner_store import jobs,execute,verify_job,index_result,finish_corner,run_store,verify_store,producer
from benchmarks.running_fault_boundary_reference import boundary_jobs

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def protocol():return read_json(ROOT/'cases/running-fault-corners/protocol.json')


def test_full_corner_recipes_keep_owner_requirements(protocol):
    e=ProtectionEnvelope.model_validate(protocol);variants=list(effective_plans(e))
    assert len(variants)==17
    nominal=list(jobs(variants[0][1]));assert len(nominal)==36
    nreq={j['scenario']:j['requirements'] for j in nominal if j['kind']=='fault'}
    for name,base,recipe in variants[1:]:
        concrete=list(jobs(base));assert len(concrete)==36
        assert {j['kind'] for j in concrete}=={'normal','fault'}
        assert {j['method'] for j in concrete}=={'radau','network_dae'}
        assert {j['grid'] for j in concrete}=={0,1,2}
        for j in concrete:
            if j['kind']=='fault':assert j['requirements']==nreq[j['scenario']]
        assert base.primary.pickup_tolerance==base.backup.pickup_tolerance==0
        assert base.primary.time_tolerance==base.backup.time_tolerance==0
        assert base.equipment==e.base.equipment


def test_boundary_coverage_and_only_declared_changes(protocol):
    original=read_json(ROOT/'cases/running-fault-protection/base-revised.json')
    variants=list(boundary_jobs(protocol,original))
    assert len(variants)==54
    assert sum(v['label']=='candidate' for v in variants)==48
    for j in variants:
        assert j['requirements']['maximum_fault_extinction_s']==.15
        assert j['study']['coupled']['faults'][0]['clear_s']==3.3
    original['primary']['pickup_a']+=1
    with pytest.raises(ValueError,match='only'):list(boundary_jobs(protocol,original))


@pytest.fixture(scope='module')
def normal_packet(tmp_path_factory):
    e=ProtectionEnvelope.model_validate(read_json(ROOT/'cases/running-fault-corners/protocol.json'))
    job=next(j for j in jobs(e.base) if j['kind']=='normal' and j['scenario']=='start' and j['method']=='radau' and j['grid']==0)
    root=tmp_path_factory.mktemp('corner-job')/'packet';r=execute((job,str(root)))
    return job,root,r


def test_sealed_job_reuse_does_not_rerun_solver(normal_packet,monkeypatch):
    import benchmarks.running_fault_corner_store as module
    job,root,result=normal_packet
    def forbidden(*a,**kw):raise AssertionError('cached study must not integrate again')
    monkeypatch.setattr(module,'run_study',forbidden)
    assert execute((job,str(root)))==result


def test_owner_change_rejected(normal_packet):
    job,root,_=normal_packet;other=copy.deepcopy(job)
    other['study']['network']['sources'][0]['r_ohm']*=2
    with pytest.raises(ValueError,match='owner'):verify_job(other,root)


def test_resealed_measurement_fails_independent_oracle(normal_packet,tmp_path):
    import shutil
    job,root,_=normal_packet;target=tmp_path/'copy';shutil.copytree(root,target)
    report=read_json(target/'trace.json')
    # Below pickup, changed CT can leave clock audit unchanged. SI oracle
    # must still reject the inconsistent measurement.
    report['channels']['primary-oc.measurement'][100]+=10
    (target/'trace.json').write_bytes(encoded(report));(target/'manifest.json').unlink();seal(target)
    result=verify_job(job,target)
    assert not result['passed'] and not result['algebra']['passed']


def test_incomplete_corner_cannot_publish_positive(protocol,tmp_path):
    e=ProtectionEnvelope.model_validate(protocol)
    with pytest.raises(ValueError,match='all36'):finish_corner(e.base,tmp_path,[])
    assert not index_result(e,[],producer())['passed']


@pytest.mark.parametrize('workers',[0,9])
def test_worker_budget(protocol,tmp_path,workers):
    with pytest.raises(ValueError,match='workers'):run_store(protocol,tmp_path/'bad',workers=workers)
    assert not (tmp_path/'bad').exists()


def test_resume_requires_same_producer_and_owner(protocol,tmp_path):
    index=tmp_path/'index';index.mkdir()
    (index/'protocol.json').write_bytes(encoded(ProtectionEnvelope.model_validate(protocol).model_dump(mode='json')))
    (index/'producer.json').write_bytes(encoded({'wrong':'producer'}))
    with pytest.raises(ValueError,match='producer'):run_store(protocol,tmp_path,resume=True)


def test_extra_layout_rejected(protocol,tmp_path):
    (tmp_path/'index').mkdir();(tmp_path/'studies').mkdir();(tmp_path/'unexpected').write_text('x')
    with pytest.raises(ValueError,match='layout'):verify_store(tmp_path,expected_protocol=protocol)


def test_joint_revision_changes_only_primary_clock(protocol):
    from benchmarks.running_fault_joint_boundary import joint_jobs
    revised=read_json(ROOT/'cases/running-fault-corners/protocol-revised.json')
    original=read_json(ROOT/'cases/running-fault-protection/base-revised.json')
    jobs=list(joint_jobs(protocol,revised,original))
    assert len(jobs)==54 and sum(j['label']=='candidate' for j in jobs)==48
    negatives=[j for j in jobs if j['label']=='original']
    assert len(negatives)==6
    for old in negatives:
        new=next(j for j in jobs if j['label']=='candidate' and all(j[k]==old[k] for k in ['corner','temperature_c','voltage_factor','method','grid']))
        assert old['requirements']==new['requirements']
        before=copy.deepcopy(old['study']);after=copy.deepcopy(new['study'])
        a=next(r for r in before['coupled']['network_automation']['relays'] if r['id']=='primary-oc')
        b=next(r for r in after['coupled']['network_automation']['relays'] if r['id']=='primary-oc')
        assert b['time_multiplier_s']==pytest.approx(.9*a['time_multiplier_s'])
        a.pop('time_multiplier_s');b.pop('time_multiplier_s')
        assert before==after
    revised['base']['primary']['pickup_a']+=1
    with pytest.raises(ValueError,match='only'):list(joint_jobs(protocol,revised,original))


def test_unexcited_isolated_row_oracle(protocol):
    from types import SimpleNamespace
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.topology import compile_topology
    from benchmarks.running_fault_island_reference import algebra
    e=ProtectionEnvelope.model_validate(protocol)
    job=next(j for j in jobs(e.base) if j['kind']=='fault')
    study=StudySpec.model_validate(job['study']);n=study.network;c=study.coupled
    top=compile_topology(n);source=n.sources[0];line=n.lines[0];fault=c.faults[0]
    channels={source.id+'.connected':[0],line.id+'.closed':[0],fault.id+'.active':[1],fault.id+'.current_a':[0]}
    for bus in top.buses:
        channels[bus+'.v']=[0];channels[bus+'.a']=[0]
    for m in c.motors:
        for key in ['connected','e_real_pu','e_imag_pu']:channels[m.id+'.'+key]=[0]
    for relay in c.network_automation.relays:channels[relay.id+'.measurement']=[0]
    report=dict(time_s=[3.1],sample_side=['regular'],channels=channels)
    assert algebra(SimpleNamespace(study=study),report)['passed']
    channels[top.terminal_bus[source.terminal_id]+'.v']=[.1]
    assert not algebra(SimpleNamespace(study=study),report)['passed']


def test_oracle_uses_physical_source_voltage_not_bus_base(protocol):
    from types import SimpleNamespace
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.topology import compile_topology
    from benchmarks.running_fault_island_reference import algebra
    e=ProtectionEnvelope.model_validate(protocol)
    job=next(j for j in jobs(e.base) if j['kind']=='fault')
    study=StudySpec.model_validate(job['study']);n=study.network;c=study.coupled
    n.sources[0].ll_v*=1.05;source=n.sources[0];line=n.lines[0];fault=c.faults[0];top=compile_topology(n)
    upstream=top.terminal_bus[source.terminal_id]
    ch={source.id+'.connected':[1],line.id+'.closed':[0],fault.id+'.active':[1],fault.id+'.current_a':[0]}
    for bus in top.buses:ch[bus+'.v']=[1.05 if bus==upstream else 0];ch[bus+'.a']=[0]
    for m in c.motors:
        for key in ['connected','e_real_pu','e_imag_pu']:ch[m.id+'.'+key]=[0]
    for relay in c.network_automation.relays:ch[relay.id+'.measurement']=[0]
    report=dict(time_s=[3.1],sample_side=['regular'],channels=ch)
    assert algebra(SimpleNamespace(study=study),report)['passed']
    ch[upstream+'.v']=[1]
    assert not algebra(SimpleNamespace(study=study),report)['passed']

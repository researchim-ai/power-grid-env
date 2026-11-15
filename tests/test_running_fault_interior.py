import copy
import math
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json
from benchmarks.running_fault_interior import InteriorProtocol,points,concrete,summary,producer
from benchmarks.running_fault_cartesian import condition_jobs

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def protocol():return read_json(ROOT/'cases/running-fault-cartesian/interior.json')


def test_seeded_strata_cover_every_bin_without_endpoints(protocol):
    p=InteriorProtocol.model_validate(protocol);a=list(points(p));b=list(points(p))
    assert a==b and len(a)==9 and a[0]==('center',[.5]*6)
    assert len({tuple(v) for _,v in a})==9
    for dim in range(6):
        values=[v[dim] for _,v in a[1:]]
        assert all(0<v<1 for v in values)
        assert {math.floor(8*v) for v in values}==set(range(8))
    other=p.model_copy(update={'seed':p.seed+1})
    assert list(points(other))!=a


def test_interior_parameters_preserve_owner_physics_and_criteria(protocol):
    p=InteriorProtocol.model_validate(protocol);original=p.model_dump(mode='json')
    owner=p.cartesian.envelope.base
    for name,coordinates in points(p):
        base,condition=concrete(p,coordinates)
        assert base.equipment==owner.equipment
        assert base.selection==owner.selection
        assert 20<=condition['conductor_temperature_c']<=90
        assert .95<=condition['source_voltage_factor']<=1.05
        for stage in ['primary','backup']:
            a=getattr(base,stage);b=getattr(owner,stage)
            assert b.pickup_a*(1-b.pickup_tolerance)<=a.pickup_a<=b.pickup_a*(1+b.pickup_tolerance)
            assert b.time_multiplier*(1-b.time_tolerance)<=a.time_multiplier<=b.time_multiplier*(1+b.time_tolerance)
            assert a.pickup_tolerance==a.time_tolerance==0
        jobs=list(condition_jobs(p.cartesian,base,condition));assert len(jobs)==36
        for job in jobs:
            if job['kind']=='fault':assert job['requirements']['maximum_fault_extinction_s']==.15
    assert p.model_dump(mode='json')==original


@pytest.mark.parametrize('strata',[0,17])
def test_invalid_strata(protocol,strata):
    protocol['strata']=strata
    with pytest.raises(ValueError):InteriorProtocol.model_validate(protocol)


def test_endpoints_required(protocol):
    protocol['cartesian']['conductor_temperatures_c']=[20,55,90]
    protocol['cartesian']['maximum_studies']=5000
    with pytest.raises(ValueError,match='exactly two'):InteriorProtocol.model_validate(protocol)


def test_incomplete_probe_set_not_positive(protocol):
    p=InteriorProtocol.model_validate(protocol)
    assert not summary(p,[],producer())['passed']


@pytest.mark.parametrize('inside',['cartesian','interior'])
def test_acceptance_cannot_modify_source_stores(protocol,tmp_path,inside):
    from benchmarks.running_fault_cartesian_acceptance import qualify
    with pytest.raises(ValueError,match='outside'):
        qualify(protocol['cartesian'],tmp_path/'cartesian',protocol,tmp_path/'interior',tmp_path/'winding',tmp_path/inside/'acceptance',workers=1)

import copy
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest
from power_grid_env.fault_temperature_envelope import calculate, run, verify_envelope, FaultEnvelope, relay_bounds
from power_grid_env.protection_design import RelaySetting
from power_grid_env.study_artifacts import seal, encoded

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def inputs():
    def load(path):return json.loads((ROOT/path).read_text())
    return dict(catalog=load('cases/equipment-catalog/catalog.json'),
                brief=load('cases/equipment-catalog/brief.json'),
                selection=load('cases/equipment-catalog/selection-250.json'),
                envelope=load('cases/fault-temperature-envelope/envelope.json'))


def test_independent_nodal_interior_grid(inputs):
    result=calculate(**inputs)
    bounds=result['current_bounds_a']
    # Independent phase-voltage nodal solve with a finite fault shunt, not
    # the producer's series-impedance expression. Fixed published 250 kVA data.
    inputs['envelope']['fault_resistance_ohm']={'minimum':.001,'maximum':.01}
    result=calculate(**inputs);bounds=result['current_bounds_a']
    samples=[]
    for temp,voltage,position,rf in itertools.product(np.linspace(20,90,5),np.linspace(.95,1.05,5),np.linspace(.01,1,9),np.linspace(.001,.01,4)):
        zs=complex(.64*.016,.64*math.sqrt(.06**2-.016**2))
        zl=position*complex(.193*(1+.00393*(temp-20))*1.05*.1,.08*.1)
        y=np.array([[1/zs+1/zl,-1/zl],[-1/zl,1/zl+1/rf]],complex)
        v=np.linalg.solve(y,np.array([400*voltage/math.sqrt(3)/zs,0]))
        current=abs(v[1]/rf);samples.append(current)
        assert bounds['minimum']-1e-8<=current<=bounds['maximum']+1e-8
    assert len(samples)==900
    assert result['minimum_witness']['conductor_c']==90
    assert result['minimum_witness']['position_pu']==1
    assert result['maximum_witness']['position_pu']==0


def test_exact_cold_hot_and_fixed_transformer(inputs):
    old=copy.deepcopy(inputs);r=calculate(**inputs)
    assert inputs==old
    cold=[c for c in r['corners'] if c['conductor_c']==20][0]
    hot=[c for c in r['corners'] if c['conductor_c']==90][0]
    assert cold['cable_r_ohm']==pytest.approx(.020265)
    assert hot['cable_r_ohm']==pytest.approx(.0258399015)
    assert not r['transformer_resistance_temperature_corrected']
    assert not r['normative_approval'] and r['reward'] is None


@pytest.mark.parametrize('curve,k,a',[('standard_inverse',.14,.02),('very_inverse',13.5,1),('extremely_inverse',80,2),('definite',None,None)])
def test_relay_interval_contains_interior(inputs,curve,k,a):
    raw=inputs['envelope']['primary'];raw['curve']=curve
    setting=RelaySetting.model_validate(raw)
    r=relay_bounds(2000,7000,setting)
    for current,pickup_factor,time_factor in itertools.product(np.linspace(2000,7000,9),np.linspace(.95,1.05,5),np.linspace(.9,1.1,5)):
        t=setting.time_multiplier*time_factor
        if k is not None:t*=k/((current/(setting.pickup_a*pickup_factor))**a-1)
        assert r['earliest_trip_s']-1e-12<=t
        assert t+setting.breaker_opening_s<=r['latest_clear_s']+1e-12


def test_no_pickup_is_json_null_and_failed_check(inputs):
    inputs['envelope']['primary']['pickup_a']=1e7
    r=calculate(**inputs)
    assert r['primary']['latest_clear_s'] is None
    assert r['duty']['primary']['conservative_i2t_a2s'] is None
    assert not r['passes_sufficient_checks']
    encoded(r)


@pytest.mark.parametrize('name,interval',[
 ('conductor_c',dict(minimum=19,maximum=90)),('conductor_c',dict(minimum=20,maximum=91)),
 ('voltage_factor',dict(minimum=.4,maximum=1)),('voltage_factor',dict(minimum=1.1,maximum=1)),
 ('position_pu',dict(minimum=-.1,maximum=1)),('position_pu',dict(minimum=0,maximum=1.1)),
 ('fault_resistance_ohm',dict(minimum=-1,maximum=0)),('fault_resistance_ohm',dict(minimum=0,maximum=float('nan')))])
def test_invalid_domains(inputs,name,interval):
    inputs['envelope'][name]=interval
    with pytest.raises(ValueError):calculate(**inputs)


def test_passport_limit_enforced(inputs):
    inputs['catalog']['cables'][0]['maximum_conductor_c']['value']=80
    inputs['catalog']['assumptions']['conductor_temperature_c']['value']=80
    with pytest.raises(ValueError,match='passport'):calculate(**inputs)


def test_artifact_owner_and_resealed_tampering(inputs,tmp_path):
    root=tmp_path/'packet';r=run(**inputs,out=root)
    assert verify_envelope(root,expected_inputs=inputs)['verified']
    other=copy.deepcopy(inputs);other['envelope']['id']='different'
    with pytest.raises(ValueError,match='owner'):verify_envelope(root,expected_inputs=other)
    r['current_bounds_a']['minimum']*=2
    (root/'result.json').write_bytes(encoded(r));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError,match='recomputed'):verify_envelope(root,expected_inputs=inputs)


def test_degenerate_point_and_pickup_equality(inputs):
    for interval in inputs['envelope'].values():
        if isinstance(interval,dict) and 'minimum' in interval:interval['maximum']=interval['minimum']
    r=calculate(**inputs)
    assert r['current_bounds_a']['minimum']==r['current_bounds_a']['maximum']
    setting=RelaySetting.model_validate(inputs['envelope']['primary'])
    assert not relay_bounds(setting.pickup_a*(1+setting.pickup_tolerance),7000,setting)['pickup_guaranteed']


def test_resistive_limit_loses_pickup_without_changing_requirements(inputs):
    inputs['envelope']['fault_resistance_ohm']['maximum']=1
    r=calculate(**inputs)
    assert not r['primary']['pickup_guaranteed']
    assert not r['backup']['pickup_guaranteed']
    assert not r['passes_sufficient_checks']
    assert r['checks']['primary_interrupting']


def test_upstream_fault_independent_of_conductor_temperature(inputs):
    inputs['envelope']['position_pu']={'minimum':0,'maximum':0}
    r=calculate(**inputs)
    for factor in [.95,1.05]:
        currents=[row['current_a'] for row in r['corners'] if row['voltage_factor']==factor]
        assert max(currents)==min(currents)

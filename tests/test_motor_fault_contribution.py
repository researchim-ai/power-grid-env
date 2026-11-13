import copy
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest
from power_grid_env.motor_fault_contribution import calculate, run, verify_contribution
from power_grid_env.study_artifacts import encoded,seal

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def snapshot():return json.loads((ROOT/'cases/motor-fault-contribution/snapshot.json').read_text())

def value(r):return complex(r['real'],r['imag'])

@pytest.mark.parametrize('position',[0,.01,.3,.99,1])
@pytest.mark.parametrize('rf',[0,.001,.1])
def test_independent_star_reduction(snapshot,position,rf):
    snapshot.update(fault_position_pu=position,fault_r_ohm=rf,downstream_load_p_w=0,downstream_load_q_var=0)
    r=calculate(snapshot)
    # Independent star reduction at the fault, eliminating endpoint buses.
    # No production motor impedance helper or nodal matrix is used.
    zs=complex(snapshot['source_r_ohm'],snapshot['source_x_ohm'])
    zl=complex(snapshot['line_r_ohm'],snapshot['line_x_ohm'])
    zm=complex(.02,.1+.1*3/3.1)*400**2/75000
    es=400/math.sqrt(3);em=complex(.95,-.05)*es
    if rf:
        vf=(es/(zs+position*zl)+em/(zm+(1-position)*zl))/(1/(zs+position*zl)+1/(zm+(1-position)*zl)+1/rf)
    else:vf=0j
    grid=(es-vf)/(zs+position*zl);motor=(em-vf)/(zm+(1-position)*zl)
    assert value(r['currents']['fault_a'])==pytest.approx(grid+motor,abs=1e-8)
    assert value(r['currents']['source_a'])==pytest.approx(grid,abs=1e-8)
    assert value(r['currents']['motor:m1_a'])==pytest.approx(motor,abs=1e-8)
    assert r['passed_algebra_audits']


def test_components_are_phasors_not_magnitude_addition(snapshot):
    snapshot['motors'][0]['emf_pu']={'real':-.95,'imag':.3}
    r=calculate(snapshot)
    total=r['currents']['fault_a']['magnitude']
    magnitudes=sum(c['currents']['fault_a']['magnitude'] for c in r['components'])
    assert magnitudes>total+100
    assert value(r['currents']['downstream_segment_a']).real<0
    assert not r['normative_approval'] and r['reward'] is None


def test_zero_emf_keeps_motor_admittance(snapshot):
    snapshot['motors'][0]['emf_pu']={'real':0,'imag':0}
    connected=calculate(snapshot)
    snapshot['motors'][0]['connected']=False
    disconnected=calculate(snapshot)
    assert connected['currents']['motor:m1_a']['magnitude']>0
    assert disconnected['currents']['motor:m1_a']['magnitude']==0
    assert connected['currents']['fault_a']['magnitude']!=disconnected['currents']['fault_a']['magnitude']


def test_multiple_motors_source_loss_and_load_kcl(snapshot):
    second=copy.deepcopy(snapshot['motors'][0]);second['id']='m2';second['emf_pu']['imag']=.2
    snapshot['motors'].append(second);snapshot['source_connected']=False
    r=calculate(snapshot)
    assert r['currents']['source_a']['magnitude']==0
    assert r['currents']['fault_a']['magnitude']>0
    assert r['passed_algebra_audits']
    assert r['components'][0]['currents']['fault_a']['magnitude']==0


def test_no_motors_matches_passive_limit(snapshot):
    snapshot.update(motors=[],downstream_load_p_w=0,downstream_load_q_var=0)
    r=calculate(snapshot)
    z=complex(snapshot['source_r_ohm'],snapshot['source_x_ohm'])+.5*complex(snapshot['line_r_ohm'],snapshot['line_x_ohm'])+.001
    assert value(r['currents']['fault_a'])==pytest.approx(400/math.sqrt(3)/z)


@pytest.mark.parametrize('field,val',[('fault_position_pu',-1),('fault_position_pu',1.1),('fault_r_ohm',-1),('source_x_ohm',0),('line_r_ohm',-1),('nominal_ll_v',float('nan'))])
def test_invalid(snapshot,field,val):
    snapshot[field]=val
    with pytest.raises(ValueError):calculate(snapshot)


def test_duplicate_and_voltage_mismatch(snapshot):
    snapshot['motors'].append(copy.deepcopy(snapshot['motors'][0]))
    with pytest.raises(ValueError,match='duplicate'):calculate(snapshot)
    snapshot['motors'].pop();snapshot['motors'][0]['rated_ll_v']=690
    with pytest.raises(ValueError,match='voltage'):calculate(snapshot)


def test_owner_and_resealed_tamper(snapshot,tmp_path):
    old=copy.deepcopy(snapshot);root=tmp_path/'packet';r=run(snapshot,root)
    assert snapshot==old and verify_contribution(root,expected_snapshot=snapshot)['verified']
    snapshot['fault_r_ohm']=.1
    with pytest.raises(ValueError,match='owner'):verify_contribution(root,expected_snapshot=snapshot)
    r['currents']['fault_a']['real']+=100
    (root/'result.json').write_bytes(encoded(r));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError,match='result'):verify_contribution(root,expected_snapshot=old)

@pytest.fixture(scope='module',params=['radau','network_dae'])
def native_trace(request):
    from benchmarks.motor_fault_contribution_reference import studies
    from power_grid_env.study_spec import StudySpec,run_study
    plan=json.loads((ROOT/'cases/closed-loop-protection/radau.json').read_text())
    _,_,_,s=next(studies(plan));d=s.model_dump(mode='json')
    c=d['coupled'];c['duration_s']=.4;c['step_s']=.002;c['integration_method']=request.param
    c['faults'][0].update(apply_s=.2,clear_s=.3)
    study=StudySpec.model_validate(d);report=run_study(study)
    return study,report


def test_native_trace_adapter_and_measurements(native_trace):
    from benchmarks.motor_fault_contribution_reference import audit
    study,report=native_trace
    r=audit(study,report)
    assert r['passed'] and r['sample_count']==7
    assert r['rows'][0]['sample_side']=='post_event'
    assert r['rows'][-1]['sample_side']=='pre_event'


@pytest.mark.parametrize('defect',['no_fault','open_line','nonbinary','truncated','bad_index'])
def test_trace_rejects_incomplete_or_unsupported(native_trace,defect):
    from power_grid_env.motor_fault_contribution import snapshot_from_report
    study,report=native_trace;report=copy.deepcopy(report)
    fault=study.coupled.faults[0].id
    index=next(i for i,a in enumerate(report['channels'][fault+'.active']) if a)
    line=study.network.lines[0].id
    if defect=='no_fault':report['channels'][fault+'.active'][index]=0
    elif defect=='open_line':report['channels'][line+'.closed'][index]=0
    elif defect=='nonbinary':report['channels'][line+'.closed'][index]=.5
    elif defect=='truncated':report['channels'][line+'.closed']=[]
    else:index=-1
    with pytest.raises(ValueError):snapshot_from_report(study,report,index)


def test_upstream_fault_line_ct_is_not_source_ct(snapshot):
    snapshot['fault_position_pu']=0
    r=calculate(snapshot)
    assert r['currents']['source_a']['magnitude']>5000
    assert r['currents']['feeder_from_a']['magnitude']<1000
    assert value(r['currents']['feeder_from_a'])==pytest.approx(value(r['currents']['feeder_to_a']))
    assert r['segment_lengths_pu']['upstream']==0


def test_downstream_fault_line_ct_equals_source_ct(snapshot):
    snapshot['fault_position_pu']=1
    r=calculate(snapshot)
    assert value(r['currents']['feeder_from_a'])==pytest.approx(value(r['currents']['source_a']))
    assert value(r['currents']['feeder_from_a'])==pytest.approx(value(r['currents']['feeder_to_a']))


def test_real_upstream_ct_audit_regression(native_trace):
    from power_grid_env.study_spec import StudySpec,run_study
    from benchmarks.motor_fault_contribution_reference import audit
    study,_=native_trace;s=study.model_dump(mode='json')
    source=s['network']['sources'][0]
    node=next(t['connectivity_node_id'] for t in s['network']['terminals'] if t['id']==source['terminal_id'])
    s['coupled']['faults'][0]['node_id']=node
    study=StudySpec.model_validate(s);report=run_study(study)
    assert audit(study,report)['passed']

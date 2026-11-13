"""PHY-21 real analytical phase checks and immutable full-matrix evidence."""
import copy
import os
from pathlib import Path
import numpy as np
import pytest
from benchmarks.network_dynamic_qualification import (
    NAMES,SCALE_NAMES,ISLAND_NAMES,AUTO_NAMES,PHASE_NAMES,study,phase_document,
    phase_reference,compare,verify_result,assessment_row,
)


@pytest.mark.parametrize('name',SCALE_NAMES)
def test_registered_mesh_has_repeated_disturbance_and_declared_equipment(name):
    s=study(name);nodes,motors,generators=map(int,name.split('_')[1:])
    assert len(s.network.connectivity_nodes)==nodes
    assert len(s.coupled.motors)==motors and len(s.coupled.machines)==generators
    assert len(s.network.lines)>nodes-1
    commands=[e for e in s.coupled.events if e.kind=='source_voltage']
    assert [(e.time_s,e.value) for e in commands]==[(.2,.7),(.3,1.),(.42,.8),(.48,1.)]
    assert s.coupled.integration_method=='network_dae' and s.coupled.jacobian_network=='direct_solve'


@pytest.mark.parametrize('name',ISLAND_NAMES+AUTO_NAMES)
def test_reordering_preserves_passports_and_requested_operations(name):
    a=study(name);b=study(name,reverse=True)
    assert {m.generator_id:m.model_dump() for m in a.coupled.machines}=={m.generator_id:m.model_dump() for m in b.coupled.machines}
    key=lambda e:(e.time_s,e.kind,e.equipment_id)
    assert sorted(map(key,a.coupled.events))==sorted(map(key,b.coupled.events))
    assert {x.id for x in a.network.lines}=={x.id for x in b.network.lines}


@pytest.mark.parametrize('name',PHASE_NAMES)
def test_exact_phase_branch_reference_has_no_state_jump_at_resistive_events(name):
    r=phase_reference(name);t=np.array(r['time_s'])
    assert len(r['channels'])==2*int(name.split('_')[1])
    for at in [.006,.012,.016]:
        i=np.flatnonzero(abs(t-at)<1e-12)
        assert len(i)==2 and [r['sample_side'][j] for j in i]==['pre_event','post_event']
        for channel in r['channels'].values():assert channel[i[0]]==channel[i[1]]
    assert max(abs(np.array(r['channels']['n0.voltage_v'])))>100
    assert not np.array_equal(r['channels']['n0.voltage_v'],r['channels']['n1.voltage_v'])


@pytest.fixture(scope='module')
def phase_packet():
    from power_grid_env.phase_network import PhaseCase,run_emt
    name='phase_12';r=run_emt(PhaseCase.model_validate(phase_document(name)),duration_s=.02,sample_s=.001,max_step_s=.0001,rtol=1e-9,atol=1e-11)
    return r,phase_reference(name)


def test_real_phase_solver_matches_exact_exponentials_and_energy(phase_packet):
    r,ref=phase_packet;check=compare('phase_12',None,r,ref)
    assert check['passed'] and len(check['errors'])==24


@pytest.mark.parametrize('defect',['voltage','current','missing_sample','incomplete','energy','side'])
def test_phase_physical_defects_cannot_qualify(phase_packet,defect):
    r,ref=copy.deepcopy(phase_packet)
    if defect=='voltage':r['channels']['n0.voltage_v'][3]+=1
    elif defect=='current':r['channels']['l0.current_a'][3]+=1
    elif defect=='missing_sample':r['time_s'].pop()
    elif defect=='incomplete':r['completed']=False
    elif defect=='energy':r['energy'][-1]['residual_j']=1e6
    else:r['sample_side'][3]='pre_event'
    try:result=compare('phase_12',None,r,ref)
    except ValueError:return
    assert not result['passed']


def test_failure_never_becomes_a_successful_resource_row():
    assert not assessment_row('phase_12',[],[],failure='worker_timeout')['passed']


@pytest.fixture(scope='module')
def corpus():
    path=os.environ.get('PGE_NETWORK_DYNAMIC_CORPUS')
    if not path:pytest.skip('set PGE_NETWORK_DYNAMIC_CORPUS for sealed full matrix tests')
    return Path(path)


def test_full_dynamic_inventory_is_recomputed(corpus):
    r=verify_result(corpus)
    assert r['verified'] and r['passed'] and r['cases']==len(NAMES) and r['full_declared_inventory_executed']


@pytest.mark.parametrize('defect',['protocol','input','rss','timing','states','producer','assembly','event','voltage','summary','repeat','inventory'])
def test_resealed_numerical_or_resource_evidence_rejected(corpus,tmp_path,defect):
    import shutil
    from power_grid_env.study_artifacts import read_json,encoded,seal
    root=tmp_path/'forged';shutil.copytree(corpus,root)
    if defect=='protocol':
        path=root/'protocol.json';r=read_json(path);r['repeats']=1
    elif defect=='input':
        path=root/'mesh_64_4_1'/'radau-0'/'study.json';r=read_json(path);r['coupled']['events'].pop()
    elif defect in {'rss','timing','states'}:
        path=root/'mesh_64_4_1'/'radau-0'/'metrics.json';r=read_json(path);r[{'rss':'current_exec_peak_rss_kib','timing':'wall_s','states':'states'}[defect]]=0
    elif defect=='producer':
        path=root/'mesh_64_4_1'/'radau-0'/'trace.json';r=read_json(path);r['code_sha256']='0'*64
    elif defect=='assembly':
        path=root/'mesh_64_4_1'/'network_dae-0'/'trace.json';r=read_json(path);r['integration']['jacobian_assembly']='finite_difference'
    elif defect=='event':
        path=root/'phase_12'/'radau-0'/'trace.json';r=read_json(path);r['event_log'].pop()
    elif defect=='voltage':
        path=root/'phase_12'/'radau-0'/'trace.json';r=read_json(path);r['channels']['n0.voltage_v'][4]+=1
    elif defect=='repeat':
        path=root/'mesh_64_4_1'/'radau-1'/'trace.json';r=read_json(path);r['channels']['m0.speed_pu'][3]+=.01
    elif defect=='inventory':
        path=root/'summary.json';r=read_json(path);r['full_declared_inventory_executed']=False
    else:
        path=root/'summary.json';r=read_json(path);r['rows']=[]
    path.write_bytes(encoded(r));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError):verify_result(root)


def test_actual_largest_points_islands_and_cascade_coverage(corpus):
    from power_grid_env.study_artifacts import read_json
    row=read_json(corpus/'mesh_2000_128_32'/'assessment.json')
    assert row['passed'] and all(c['all_node_count']==2000 for c in row['checks'])
    assert row['resources']['network_dae']['dynamic_states']==960
    for name in ISLAND_NAMES:
        row=read_json(corpus/name/'assessment.json')
        assert row['passed'] and len(row['checks'])==5
        assert all(c['maximum_actual_islands']==3 for c in row['checks'])
    failure=read_json(corpus/'automation_breaker_failure'/'network_dae-0'/'trace.json')
    assert any(e['kind']=='breaker_failure_confirmed' for e in failure['event_log'])
    assert any(e['kind']=='breaker_open' and e.get('equipment_id')=='grid-cb' and e.get('outcome')=='applied' for e in failure['event_log'])
    for name in AUTO_NAMES[1:]:
        row=read_json(corpus/name/'assessment.json')
        assert row['passed'] and all(c['break_before_make'] for c in row['checks'])
    phase=read_json(corpus/'phase_96'/'assessment.json')
    assert phase['passed'] and phase['resources']['radau']['dynamic_states']==192
    assert all(len(c['errors'])==192 for c in phase['checks'])


def test_phase_command_capacity_accepts_512_and_rejects_513():
    from power_grid_env.phase_network import PhaseCase
    p=phase_document('phase_12')
    assert PhaseCase.model_json_schema()['properties']['events']['maxItems']==512
    p['events']=[dict(time_s=(k+1)*.00002,kind='resistance',equipment_id='r0',value=20.) for k in range(512)]
    assert len(PhaseCase.model_validate(p).events)==512
    p['events'].append(dict(time_s=.015,kind='resistance',equipment_id='r0',value=20.))
    with pytest.raises(ValueError):PhaseCase.model_validate(p)


def test_expanded_phase_event_capacity_keeps_ambiguity_and_output_budgets():
    from power_grid_env.phase_network import PhaseCase,run_emt
    p=phase_document('phase_96');case=PhaseCase.model_validate(p)
    assert len(case.events)==288
    with pytest.raises(ValueError,match='sample/step budget'):
        run_emt(case,duration_s=.02,sample_s=1e-6,max_step_s=.0001)
    p['events'].append(copy.deepcopy(p['events'][0]))
    with pytest.raises(ValueError,match='ambiguous simultaneous'):PhaseCase.model_validate(p)


@pytest.mark.parametrize('field,reason',[
    ('maximum_rhs_evaluations','rhs_evaluations'),
    ('maximum_event_batches','event_batches'),
    ('maximum_retained_scalars','retained_scalars'),
])
def test_real_dae_budget_exhaustion_is_not_a_completed_physical_study(field,reason):
    from power_grid_env.study_spec import run_study
    s=study('mesh_64_4_1');setattr(s.coupled.runtime_limits,field,1)
    r=run_study(s)
    assert r['status']=='runtime_limit_exceeded' and not r['completed']
    assert r['resource_limit']['resource']==reason
    assert r['resource_limit']['attempted']>r['resource_limit']['limit']
    with pytest.raises(ValueError,match='completed trajectories'):compare('mesh_64_4_1',s,r,{'completed':True})


def test_cli_rejects_initialized_but_budget_aborted_study(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.study_artifacts import encoded,read_json
    s=study('mesh_64_4_1');s.coupled.runtime_limits.maximum_rhs_evaluations=1
    source=tmp_path/'study.json';out=tmp_path/'result.json';source.write_bytes(encoded(s.model_dump(mode='json')))
    assert main(['run-study',str(source),'--out',str(out)])==2
    result=read_json(out)
    assert result['solved'] and not result['completed'] and result['status']=='runtime_limit_exceeded'

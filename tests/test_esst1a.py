"""ESST1A typed connections, raw-state resets and independent native feedback."""
from functools import lru_cache
from pathlib import Path
import json,os
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.exciter_esst1a import ESST1APassport,PORTS,STATES,VALUES,quantities
from power_grid_env.coupled_rms import ESST1APortEvent,validate_coupled
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.generator_esst1a_cases import NAMES,example

@pytest.fixture(scope='module')
def primary():
    @lru_cache(None)
    def result(name):
        s=example(name);r=run_study(s);assert r['completed'],r
        return s,r
    return result

@pytest.mark.parametrize('name',NAMES)
def test_loaded_network_initialization_and_complete_channels(name,primary):
    s,r=primary(name)
    assert r['operating_point']['checked_dynamic_residual_per_s']<1e-8
    for p in s.coupled.esst1a_exciters:
        prefix=p.generator_id+'.ESST1A.'
        assert {prefix+k for k in (*PORTS,*STATES,*VALUES)}<=r['channels'].keys()
        initial=r['operating_point']['ESST1A_initialization'][p.generator_id]
        assert initial['initial_gates']=='strictly_inactive'
        assert np.isfinite([r['channels'][prefix+k] for k in STATES]).all()
    json.dumps(r,allow_nan=False)

@pytest.mark.parametrize('name',NAMES)
def test_unmodified_native_ESST1A_in_independent_closed_network(name,primary,tmp_path):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica/OpenIPSL runtime')
    from benchmarks.esst1a_network import run
    from benchmarks.generator_esst1a_validation import compare
    s,a=primary(name);b=run(s,tmp_path/'native',Path(root),1e-10);check=compare(s,a,b)
    assert check['passed'],{k:v for k,v in check['errors'].items() if v>check['limits'][k]}
    assert b['native_ESST1A_in_feedback'] and not b['production_AVR_RHS_used']
    assert all(q['initial_residual_per_s']<1e-8 for q in b['exciter_initialization'].values())

@pytest.mark.parametrize('name',['hold','reference_up','reference_down','oel_takeover','uel_error','uel_field','gate_priority','supplementary_voltage','supplementary_field','regulator_reset','current_limit','rectifier_limit'])
def test_native_component_all_states_outputs_and_gate_ownership(name,tmp_path):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica/OpenIPSL runtime')
    from benchmarks.esst1a_component import scenario,primary,native,compare
    p,inputs,events=scenario(name);a=primary(p,inputs,events,1e-10);b=native(p,inputs,events,tmp_path/'native',Path(root),1e-10);c=compare(a,b)
    assert c['passed'],c['errors']
    if name=='regulator_reset':
        assert max(r['channels']['regulator_raw_pu'] for r in a['rows'])>p.V_AMAX+.1
        assert any(e['kind']=='esst1a_regulator_reset' for e in a['events'])
        assert any(abs(e['raw_before']-e['raw_after'])>.01 for e in b['transitions'])

@pytest.mark.parametrize('port',['oel_ceiling_pu','uel_field_floor_pu'])
@pytest.mark.parametrize('value',[-.1,float('inf'),float('nan')])
def test_absolute_field_ports_reject_invalid_units_or_nonfinite(port,value):
    with pytest.raises(ValidationError):ESST1APortEvent(time_s=.2,equipment_id='g2',port=port,value=value)

@pytest.mark.parametrize('change',['duplicate','unknown','SEXS','OEL2C','no_governor','bad_port_target'])
def test_incompatible_connections_rejected_before_simulation(change):
    s=example('hold')
    if change=='duplicate':s.coupled.esst1a_exciters*=2
    if change=='unknown':s.coupled.esst1a_exciters[0].generator_id='missing'
    if change=='SEXS':s.coupled.machines[1].exciter=example('hold').coupled.machines[0].exciter
    if change=='OEL2C':
        from benchmarks.generator_oel2c_selected_cases import example as old
        s.coupled.oel2c_limiters=old('inactive').coupled.oel2c_limiters
    if change=='no_governor':
        # Fixed mechanical power remains supported in production; native adapter
        # deliberately has the narrower TGOV1 scope.
        from benchmarks.esst1a_network import scope
        s.coupled.machines[1].governor=None
        with pytest.raises(ValueError):scope(s)
        return
    if change=='bad_port_target':s.coupled.events=[ESST1APortEvent(time_s=.2,equipment_id='g',port='oel_ceiling_pu',value=1.)]
    assert not validate_coupled(s.network,s.coupled)['ok']

def test_simultaneous_different_ports_atomic_and_duplicate_port_rejected():
    s=example('gate_priority');StudySpec.model_validate(s.model_dump(mode='json'))
    s.coupled.events.append(s.coupled.events[0].model_copy())
    with pytest.raises(ValidationError):StudySpec.model_validate(s.model_dump(mode='json'))

def test_OEL_ceiling_wins_UEL_floor_and_states_are_continuous(primary):
    _,r=primary('gate_priority');ch=r['channels'];indices=[i for i,(t,side) in enumerate(zip(r['time_s'],r['sample_side'])) if abs(t-.2)<1e-12 and side in ('pre_event','post_event')]
    assert len(indices)==2;a,b=indices
    assert ch['g2.ESST1A.field_pu'][b]==pytest.approx(.7)
    assert ch['g2.ESST1A.oel_active'][b]==1
    for key in STATES:assert ch['g2.ESST1A.'+key][a]==ch['g2.ESST1A.'+key][b]

def test_thermal_trip_actuator_does_not_erase_AVR_memory(primary):
    _,r=primary('field_trip');ch=r['channels'];off=np.asarray(ch['g2.field_voltage_enabled'])==0
    assert off.any() and np.all(np.asarray(ch['g2.field_pu'])[off]==0)
    assert np.any(np.asarray(ch['g2.ESST1A.field_pu'])[off]>0)

@pytest.mark.parametrize('data',[dict(T_R=0),dict(K_F=0),dict(T_C=.2),dict(T_C1=.3),dict(V_RMIN=4),dict(V_AMIN=4),dict(K_C=float('nan'))])
def test_degenerate_or_nonfinite_passport_outside_profile(data):
    with pytest.raises(ValidationError):ESST1APassport(generator_id='g',**data)

def test_inverted_rectifier_limits_fail_instead_of_silent_clamp():
    p=ESST1APassport(generator_id='g');ports={key:getattr(p,key,0.) for key in PORTS}
    with pytest.raises(ValueError,match='inverted'):quantities(p,np.array([1.,.1,.1,1.,1.]),.01,10.,1.1,ports)

def test_public_modal_analysis_accepts_qualified_interior_ESST1A():
    from power_grid_env.coupled_small_signal import run_coupled_small_signal
    s=example('hold');r=run_coupled_small_signal(s.network,s.coupled)
    assert r['completed'] and r['branch_preserving_steps']

def test_old_summation_event_shape_does_not_acquire_port():
    from power_grid_env.coupled_rms import CoupledEvent
    e=CoupledEvent(kind='voltage_reference_step',time_s=.2,equipment_id='g',value=.01)
    assert 'port' not in e.model_dump()

def test_live_UEL_enters_then_releases_after_reference_recovery(primary):
    _,r=primary('uel_summation');values=r['channels']['g2.UEL.output_pu']
    assert max(values)>1e-4 and values[-1]<1e-8

@pytest.mark.parametrize('field,value',[('oel_ceiling_pu',.5),('uel_error_floor_pu',.19),('uel_field_floor_pu',2.),('V_AMAX',.5),('V_RMAX',.5)])
def test_infeasible_initial_controller_gates_return_no_success_trace(field,value):
    s=example('hold');setattr(s.coupled.esst1a_exciters[0],field,value)
    r=run_study(s);assert not r['completed'] and 'channels' not in r

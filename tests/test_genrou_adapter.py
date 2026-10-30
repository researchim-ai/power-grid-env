"""Six-state machine mapping, independent trajectory and mixed-model identities."""
from pathlib import Path
import numpy as np
import pytest
from test_canonical_rms import example
from test_regulated_canonical_rms import controlled
from power_grid_env.rms_study import RoundRotorMachine,RMSEvent,RMSFault
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.genrou_reference import independent_reference,saturation_curve


def genrou(generator_id='g',saturated=False):
    return RoundRotorMachine(generator_id=generator_id,rated_va=100e6,inertia_s=4.,x_d_prime_pu=.3,
        x_d_pu=1.8,x_q_pu=1.7,x_d_subtransient_pu=.25,x_q_prime_pu=.55,x_q_subtransient_pu=.25,
        leakage_pu=.15,armature_r_pu=.002,td_prime_s=8.,td_subtransient_s=.04,
        tq_prime_s=.8,tq_subtransient_s=.02,saturation='quadratic' if saturated else 'disabled',
        saturation_s10=.1 if saturated else None,saturation_s12=.4 if saturated else None)


def spec(saturated=False):
    s=example(step=.001);s.rms.machines=[genrou(saturated=saturated)]
    s.rms.faults=[RMSFault(node_id='gen',apply_s=.2,clear_s=.25,r_ohm=0.,x_ohm=24.2)]
    return s


@pytest.mark.parametrize('saturated',[False,True])
def test_six_state_fault_trajectory_matches_independent_rk(saturated):
    s=spec(saturated)
    s=StudySpec.model_validate_json(s.model_dump_json())
    assert isinstance(s.rms.machines[0],RoundRotorMachine)
    r=run_study(s);assert r['completed'],r
    expected=independent_reference(s,r['time_s'])
    for i,name in enumerate(['delta','omega','e1q','e1d','e2d','e2q']):
        limit=1e-5 if name=='omega' else 1e-4 if name=='delta' else 1e-3
        assert max(abs(expected[:,i]-r['channels']['g.'+name]))<limit
    assert r['mapping']['machine_models']['g']=='GENROU'
    assert r['industrial_profile_qualified'] is False
    if saturated:
        m=s.rms.machines[0]
        assert saturation_curve(m,1.)==pytest.approx(.1)
        assert saturation_curve(m,1.2)==pytest.approx(.4)
        assert max(r['channels']['g.Se'])>0
    else:assert max(abs(np.array(r['channels']['g.Se'])))<1e-10


def test_genrou_steady_state_and_system_base_invariance():
    s=spec();s.rms.events=[];s.rms.faults=[]
    quiet=run_study(s);assert quiet['completed']
    for name in ['delta','omega','e1q','e1d','e2d','e2q']:
        assert np.ptp(quiet['channels']['g.'+name])<1e-8
    power=complex(quiet['channels']['g.p_w'][0],quiet['channels']['g.q_var'][0])/100e6
    current=abs(power)/1.05
    assert quiet['channels']['g.tm'][0]-power.real==pytest.approx(.002*current**2,abs=1e-9)
    s=spec();a=run_study(s)
    s.network.s_base_va=50e6;b=run_study(s)
    assert a['completed'] and b['completed']
    for name in ['delta','omega','e1q','e1d','e2d','e2q']:
        assert max(abs(np.interp(a['time_s'],b['time_s'],b['channels']['g.'+name])-a['channels']['g.'+name]))<1e-6


def test_mixed_models_keep_ids_and_trip_the_correct_machine():
    s=StudySpec.model_validate_json(Path('cases/studies/rms_two_machine.json').read_text())
    first=s.network.generators[0].id;second=s.network.generators[1].id
    s.rms.machines[0]=genrou(first)
    s.rms.events=[RMSEvent(time_s=.1,kind='generator_trip',equipment_id=first)]
    r=run_study(s);assert r['completed'],r
    assert r['mapping']['machine_models']=={first:'GENROU',second:'GENCLS'}
    assert abs(r['channels'][first+'.p_w'][-1])<1.
    assert abs(r['channels'][second+'.p_w'][-1])>1e6
    assert first+'.e1q' in r['channels'] and second+'.e1q' not in r['channels']


def test_genrou_controls_and_isolated_worker_keep_same_trajectory():
    s=controlled();m=genrou();old=s.rms.machines[0]
    m.governor=old.governor;m.exciter=old.exciter;m.stabilizer=old.stabilizer
    s.rms.machines=[m]
    s.rms.faults=[RMSFault(node_id='gen',apply_s=.4,clear_s=.46,r_ohm=0.,x_ohm=24.2)]
    a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert a['channels']['g.e1q']==b['channels']['g.e1q']
    assert np.ptp(a['channels']['g.SEXS.vout'])>.01
    assert a['control_limit_violations']==[]


def test_inconsistent_and_missing_genrou_data_rejected_before_backend():
    data=genrou().model_dump()
    for update in [{'x_d_subtransient_pu':.35},{'x_q_subtransient_pu':.26},
                   {'td_subtransient_s':9.},{'saturation':'quadratic'},
                   {'saturation_s10':.1},{'armature_r_pu':-1.}]:
        with pytest.raises(ValueError):RoundRotorMachine.model_validate({**data,**update})
    conflicting=spec();conflicting.network.generators[0].x_d_pu=1.9
    assert run_study(conflicting)['reason']=='rms_scope'
    incomplete=spec().model_dump(mode='json');del incomplete['rms']['machines'][0]['td_prime_s']
    from power_grid_env.study_spec import run_study_document
    assert run_study_document(incomplete)['reason']=='schema'

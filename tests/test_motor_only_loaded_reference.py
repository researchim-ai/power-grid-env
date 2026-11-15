from pathlib import Path
import copy
import numpy as np
import pytest
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.motor_only_loaded_reference import WindingReference
from benchmarks.running_motor_winding_reference import compare

ROOT=Path(__file__).resolve().parents[1]

def example(mode='hold'):
    return read_json(ROOT/f'cases/motor-only-loaded/T90p0-V0p95-equilibrium-{mode}-network_dae-grid0.json')


@pytest.fixture(scope='module')
def hold():
    study=example();report=run_study(StudySpec.model_validate(study));return study,report


def test_independent_T_root_matches_network_and_native_state(hold):
    study,report=hold;reference=WindingReference(study);state,initial=reference.initialize()
    assert initial['speed_pu']==pytest.approx(report['channels']['m.speed_pu'][0],abs=1e-10)
    assert initial['voltage_pu']==pytest.approx(report['channels']['m.bus_voltage_pu'][0],abs=1e-10)
    assert max(abs(reference.rhs(state)))<1e-8
    assert max(np.array(initial['eigenvalues_per_s'])[:,0])<0


@pytest.mark.parametrize('mode',['plant_fault','grid_fault'])
def test_independent_loaded_fault_trajectory(mode):
    study=example(mode);report=run_study(StudySpec.model_validate(study))
    rows,initial=WindingReference(study).trajectory(report['time_s'],report['sample_side'])
    assert compare(study,report,rows)['passed']
    for f in study['coupled']['faults']:
        assert max(abs(row['fault_current_a'][f['id']]-report['channels'][f['id']+'.current_a'][i]) for i,row in enumerate(rows))<1e-3
    assert initial['speed_pu']>.9


def test_reference_does_not_call_native_initializer_or_dynamics(hold,monkeypatch):
    import power_grid_env.coupled_initialization as module
    from power_grid_env.coupled_rms import CoupledNetwork
    def forbidden(*a,**k):raise AssertionError('native equilibrium/dynamics must not condition reference')
    monkeypatch.setattr(module,'initialize_coupled',forbidden)
    monkeypatch.setattr(CoupledNetwork,'static_electrical',forbidden)
    monkeypatch.setattr(CoupledNetwork,'physical_rhs',forbidden)
    study,report=hold;values,_=WindingReference(study).trajectory(report['time_s'],report['sample_side'])
    assert compare(study,report,values)['passed']
    damaged=copy.deepcopy(report);damaged['channels']['m.e_real_pu'][0]+=.1
    assert not compare(study,damaged,values)['passed']


def test_reference_zero_load_boundary_and_infeasible_high_speed_branch():
    study=example();study['coupled']['motors'][0]['fan_torque_pu']=0
    _,initial=WindingReference(study).initialize();assert initial['speed_pu']==1
    study['coupled']['motors'][0]['fan_torque_pu']=1000
    with pytest.raises(ValueError,match='no bracketed'):WindingReference(study).initialize()


def test_active_fault_protection_not_silently_replayed():
    study=example('plant_fault');study['coupled']['network_automation']['relays'][0]['pickup']=1
    with pytest.raises(ValueError,match='active network relay'):WindingReference(study).trajectory([0,.1,.1,.6],['initial','pre_event','post_event','regular'])

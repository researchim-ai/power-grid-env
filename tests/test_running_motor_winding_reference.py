import copy
import math
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec,run_study
from benchmarks.running_fault_cartesian import CartesianProtocol,conditions,condition_jobs
from benchmarks.running_motor_winding_reference import reference,compare

ROOT=Path(__file__).resolve().parents[1]

def normal(mode='start'):
    p=CartesianProtocol.model_validate(read_json(ROOT/'cases/running-fault-cartesian/protocol.json'))
    _,c=next(conditions(p))
    return next(j for j in condition_jobs(p,p.envelope.base,c) if j['kind']=='normal' and j['scenario']==mode and j['method']=='network_dae' and j['grid']==0)


def test_disconnected_machine_matches_passive_voltage_and_exact_cooling():
    job=normal();study=job['study'];m=study['coupled']['motors'][0];m['connected']=False;m['thermal']['initial_level']=1
    n=study['network'];source=n['sources'][0];line=n['lines'][0]
    rows=reference(study,[0,1,4],['initial','regular','regular'])
    z=complex(source['r_ohm'],source['x_ohm'])+complex(line['r_ohm'][0][0],line['x_ohm'][0][0])
    y=sum(complex(sum(l['p_w'].values()),-sum(l['q_var'].values())) for l in n['loads'])/400**2
    expected=(source['ll_v']/400)/(1+z*y)
    for t,row in zip([0,1,4],rows):
        assert row['current_pu']==0 and row['speed_pu']==0
        assert row['thermal_level']==pytest.approx(math.exp(-t/5),rel=1e-10)
        assert complex(row['downstream_voltage_real_pu'],row['downstream_voltage_imag_pu'])==pytest.approx(expected)


@pytest.mark.parametrize('mode',['coast_restart','repeated_sag'])
def test_scheduled_jumps_preserve_flux_speed_heat(mode):
    rows=reference(normal(mode)['study'],[0,1,1,1.2,1.2,4],['initial','pre_event','post_event','pre_event','post_event','regular'])
    for a,b in [(rows[1],rows[2]),(rows[3],rows[4])]:
        for key in ['rotor_flux_real_vs','rotor_flux_imag_vs','speed_pu','thermal_level']:
            assert a[key]==pytest.approx(b[key],abs=1e-12)
    if mode=='coast_restart':
        assert rows[1]['current_pu']>0 and rows[2]['current_pu']==0
        assert rows[3]['current_pu']==0 and rows[4]['current_pu']>0
        assert abs(complex(rows[4]['emf_real_pu'],rows[4]['emf_imag_pu']))>0


def test_active_protection_is_explicitly_outside_reference():
    study=normal()['study'];study['coupled']['network_automation']['relays'][0]['pickup']=1
    with pytest.raises(ValueError,match='overcurrent'):reference(study,[0,.1],['initial','regular'])


@pytest.fixture(scope='module')
def native():
    job=normal();report=run_study(StudySpec.model_validate(job['study']));return job,report


def test_independent_reference_does_not_call_production_dynamics(native,monkeypatch):
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.induction_network import InductionNetwork
    def forbidden(*a,**kw):raise AssertionError('production dynamics forbidden in independent reference')
    monkeypatch.setattr(CoupledNetwork,'physical_rhs',forbidden)
    monkeypatch.setattr(InductionNetwork,'motor_derivatives',forbidden)
    job,report=native;values=reference(job['study'],report['time_s'],report['sample_side'])
    assert compare(job['study'],report,values)['passed']


def test_corrupted_native_emf_does_not_condition_reference(native):
    job,report=native;values=reference(job['study'],report['time_s'],report['sample_side'])
    damaged=copy.deepcopy(report);damaged['channels']['m.e_real_pu'][100]+=.1
    assert not compare(job['study'],damaged,values)['passed']
    assert values==reference(job['study'],damaged['time_s'],damaged['sample_side'])

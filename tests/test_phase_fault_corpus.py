import pytest
from pydantic import ValidationError
from benchmarks.phase_fault_corpus import CASES,example,check,reference
from power_grid_env.model import StudyFault
from power_grid_env.backend_opendss import solve_network

@pytest.mark.parametrize('mesh,kind,phases',CASES)
def test_asymmetric_phase_faults_match_independent_dense_MNA(mesh,kind,phases):
    result=check(example(mesh,kind,phases));assert result['passed'],result


def test_shared_ground_LLG_is_not_two_independent_ground_impedances():
    a=example(True,'llg_common',['A','B']);b=example(True,'llg',['A','B'])
    x=reference(a)['voltage'];y=reference(b)['voltage']
    assert abs(x-y).max()>10.

@pytest.mark.parametrize('defect',['unknown_bus','line_without_position','duplicate_phase','nan_impedance','duplicate_id'])
def test_fault_is_never_silently_dropped_or_merged(defect):
    n=example(False,'slg',['A']);f=n.study_faults[0]
    if defect=='unknown_bus':f.bus_id='unknown'
    elif defect=='line_without_position':f.bus_id=None;f.line_id='l1'
    elif defect=='duplicate_phase':f.kind='ll';f.phases=['A','A']
    elif defect=='nan_impedance':f.r_ohm=float('nan')
    else:n.study_faults.append(f.model_copy(deep=True))
    result=solve_network(n);assert not result.converged and result.status=='invalid_model'
    assert result.diagnostics['fault_errors']


def test_common_LLG_requires_explicit_passive_phase_legs():
    with pytest.raises(ValidationError):StudyFault(id='x',bus_id='n',kind='llg_common',phases=['A','B'],r_ohm=1.)


def test_common_fault_exports_each_phase_leg_and_shared_ground_separately():
    n=example(True,'llg_common',['B','C']);r=solve_network(n);assert r.converged
    def current(id_):return next(x.current for x in r.flows if x.equipment_id==id_ and x.terminal_index==0)
    assert abs(current('fault:leg:B')+current('fault:leg:C')-current('fault:ground'))<1e-6

"""Hot-path optimizations retain the frozen network and magnetic contract."""
import itertools
import numpy as np
import pytest
from benchmarks.extended_closed_loop_acceptance import document
from benchmarks.switching_hotpath_reference import LegacyDrive,LegacyMagnetics
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy
from power_grid_env.switching_magnetics import SwitchingMagnetics


@pytest.mark.parametrize('name',['double_cage','saturated_single_core','saturated_four_core','warm_four_winding'])
@pytest.mark.parametrize('phase',list(itertools.product([0,1,2],repeat=3)))
@pytest.mark.parametrize('h',[1e-5,3.90625e-8])
def test_all_admissible_switches_keep_full_coupled_candidate(name,phase,h):
    p=SwitchingDriveStudy.model_validate(document(name));a=SwitchingDrive(p);b=LegacyDrive(p)
    for m in [a,b]:
        m.flux[:]=np.tile([.3,.1],len(p.motor.cages)+1);m.line[:]=[1.,-.5,-.5];m.speed=20.
    gates=np.array([[s==1,s==2] for s in phase]);x=a.candidate(.01,h,gates);y=b.candidate(.01,h,gates)
    assert x.keys()==y.keys()
    for key in x:
        np.testing.assert_allclose(x[key],y[key],rtol=1e-11,atol=1e-10,err_msg=key)
    assert a.newton_calls==b.newton_calls and a.linear_direct_solves==b.linear_direct_solves and a.linear_least_squares_solves==b.linear_least_squares_solves


@pytest.mark.parametrize('scale',[1.,4.,10.])
def test_residual_and_potential_line_search_paths_match_previous_solver(scale):
    p=SwitchingDriveStudy.model_validate(document('double_cage'));a=SwitchingDrive(p);b=LegacyDrive(p)
    for m in [a,b]:
        m.flux[:]=np.tile([.3,.1],len(p.motor.cages)+1);m.speed=10.;original=m._network_correction
        m._network_correction=lambda *args,original=original:scale*original(*args)
    gates=np.array([[1,0],[0,1],[0,1]],bool);x=a.candidate(.01,1e-7,gates);y=b.candidate(.01,1e-7,gates)
    for key in x:np.testing.assert_allclose(x[key],y[key],rtol=1e-11,atol=1e-10,err_msg=key)
    assert a.newton_calls==b.newton_calls


@pytest.mark.parametrize('name',['double_cage','saturated_single_core','saturated_four_core','warm_four_winding'])
def test_magnetic_state_outputs_and_material_arrays_keep_their_contract(name):
    p=SwitchingDriveStudy.model_validate(document(name));a=SwitchingMagnetics(p.motor);b=LegacyMagnetics(p.motor)
    old=np.tile([.4,.1],a.n);mid=old+np.tile([.001,-.0002],a.n);temperature=[90.,70.] if p.motor.thermal else None
    np.testing.assert_array_equal(a.magnetic_currents(old),b.magnetic_currents(old))
    x=a.affine(old,1e-6,35.,mid,temperature);y=b.affine(old,1e-6,35.,mid,temperature)
    for k in x:np.testing.assert_array_equal(x[k],y[k])
    x=a.stage(old,mid,1e-6,np.array([100.,30.]),mid-old,temperature);y=b.stage(old,mid,1e-6,np.array([100.,30.]),mid-old,temperature)
    for k in x:np.testing.assert_array_equal(x[k],y[k])

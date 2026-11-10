import itertools
import numpy as np
import pytest
from benchmarks.closed_loop_acceptance import document
from benchmarks.switching_thermal_cases import document as thermal_document
from benchmarks.mna_legacy_reference import LegacyDrive
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy


def model(name='nominal'):
    return SwitchingDrive(SwitchingDriveStudy.model_validate(document(name)))


@pytest.mark.parametrize('phases',list(itertools.product([0,1,2],repeat=3)))
def test_every_admissible_gate_pattern_keeps_full_network_physics(phases):
    p=SwitchingDriveStudy.model_validate(document('nominal'));a=SwitchingDrive(p);b=LegacyDrive(p)
    for m in [a,b]:
        m.flux[:]=[.3,.1,.28,.09];m.line[:]=[1.,-.5,-.5];m.speed=20.
    gates=np.array([[s==1,s==2] for s in phases])
    x=a.candidate(.01,1e-5,gates);y=b.candidate(.01,1e-5,gates)
    for key in ['flux_mid','line_mid','currents_mid','heat','temperature']:
        np.testing.assert_allclose(x[key],y[key],atol=1e-7,rtol=1e-8)
    assert x['speed_mid']==pytest.approx(y['speed_mid'],abs=1e-8)
    np.testing.assert_allclose(x['voltage'][:5],y['voltage'][:5],atol=1e-6)
    # The isolated star common mode can be a gauge. Line-line voltage is physical.
    np.testing.assert_allclose(x['voltage'][5:]-np.mean(x['voltage'][5:]),y['voltage'][5:]-np.mean(y['voltage'][5:]),atol=1e-6)


def test_floating_star_keeps_rank_tolerant_gauge_without_added_conductance():
    m=model();jac=np.eye(8);jac[5:,5:]=np.array([[2.,-1.,-1.],[-1.,2.,-1.],[-1.,-1.,2.]])
    residual=np.array([1.,2.,3.,4.,5.,1.,-1.,0.]);step=m._network_correction(jac,residual,False)
    np.testing.assert_allclose(jac@step,-residual,atol=1e-12)
    assert abs(sum(step[5:]))<1e-12 and m.linear_direct_solves==0 and m.linear_least_squares_solves==1


def test_anchored_network_uses_direct_solve_with_same_residual():
    m=model();rng=np.random.default_rng(5);A=rng.normal(size=(8,8));jac=A.T@A+np.eye(8);r=rng.normal(size=8)
    x=m._network_correction(jac,r,True)
    np.testing.assert_allclose(jac@x,-r,atol=1e-12)
    assert m.linear_direct_solves==1 and m.linear_least_squares_solves==0


@pytest.mark.parametrize('failure',['singular','nonfinite','inaccurate'])
def test_direct_solver_failure_retries_existing_svd_path(monkeypatch,failure):
    m=model();jac=np.eye(8);residual=np.ones(8)
    def solve(*args):
        if failure=='singular':raise np.linalg.LinAlgError('test singular')
        return np.full(8,np.nan) if failure=='nonfinite' else np.zeros(8)
    monkeypatch.setattr(np.linalg,'solve',solve)
    np.testing.assert_allclose(m._network_correction(jac,residual,True),-residual)
    assert m.linear_direct_fallbacks==1 and m.linear_least_squares_solves==1


def test_context_reused_only_within_one_coupled_trial(monkeypatch):
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(thermal_document('saturated_four_thermal')));original=m._network_context;calls=[]
    def count(t,h,gates):
        calls.append((t,h));return original(t,h,gates)
    monkeypatch.setattr(m,'_network_context',count);g=np.array([[1,0],[0,1],[0,1]],bool)
    before=m.temperature.copy();first=m.candidate(.01,1e-5,g)
    assert len(calls)==1
    m.temperature[1]+=10.;second=m.candidate(.01,1e-5,g)
    assert len(calls)==2 and not np.array_equal(first['voltage'],second['voltage'])
    m.temperature=before


def test_source_fast_path_reads_changed_envelope_values():
    m=model();p=m.p
    for points in [[(0.,.8)],[(0.,1.),(.02,.4)]]:
        p.grid_envelope=points
        expected=np.sqrt(2)*p.grid_ln_rms_v*np.interp(.01,*np.array(points).T)*np.cos(2*np.pi*p.grid_frequency_hz*.01+np.array([0.,-2*np.pi/3,2*np.pi/3]))
        np.testing.assert_allclose(m.source(.01),expected,rtol=1e-14,atol=1e-12)

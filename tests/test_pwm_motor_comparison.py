import copy
import numpy as np
import pytest
from benchmarks.pwm_motor_comparison import protocol,waveform,native,compare,energy,NAMES


def test_registered_scope_and_unsupported_case():
    for name in NAMES:
        p=protocol(name)
        assert len(p['motor']['cages'])==1 and not p['motor'].get('thermal')
        assert p['reference']['version']=='0.19.0'
    with pytest.raises(ValueError):protocol('easy-substitute')


@pytest.mark.parametrize('name',NAMES)
def test_waveform_covers_full_horizon_without_gaps_and_has_two_level_vectors(name):
    p=protocol(name);s=waveform(p)
    assert s[0][0]==0 and s[-1][1]==p['duration_s']
    for (left,right,u),following in zip(s,s[1:]):
        assert left<right and right==following[0]
        assert min(abs(abs(u)-v) for v in [0.,2/3*p['dc_v']])<1e-10
    if name=='saturated_sag':
        assert all(abs(u)<1e-12 for left,right,u in s if .015<=left<.02)
        assert any(abs(u)>1 for left,right,u in s if left>.025)


def test_native_midpoint_energy_balance_with_regenerative_initial_stock():
    p=protocol('regeneration');p['duration_s']=.002;p['sample_s']=.0002
    r=native(p,1e-5)
    assert len(r['rows'])==11 and r['rows'][0][6]==180.
    assert r['maximum_energy_residual_j']<1e-7
    assert energy(p['motor'],np.zeros(4),180.)>0


def test_comparison_requires_all_eight_channels_and_exact_axes():
    a=dict(time_s=[0.,1.],rows=np.zeros((2,8)).tolist())
    assert compare(a,a)['passed']
    b=copy.deepcopy(a);b['rows'][1][4]=1.
    assert not compare(b,a)['passed']
    b=copy.deepcopy(a);b['time_s'][1]=.9
    with pytest.raises(ValueError):compare(b,a)
    b=copy.deepcopy(a);b['rows']=[[0.]*7]*2
    with pytest.raises(ValueError):compare(b,a)
    b=copy.deepcopy(a);b['rows'][1][6]=float('nan')
    with pytest.raises(ValueError):compare(b,a)


def test_energy_is_nonnegative_with_saturation_and_zero_at_rest():
    p=protocol('saturated_pwm')['motor']
    assert energy(p,np.zeros(4),0.)==0
    assert energy(p,[.6,.1,.55,.12],10.)>0


def test_same_truncated_outputs_cannot_pass_registered_horizon():
    p=protocol('linear_pwm');a=dict(time_s=[0.,.0002],rows=np.zeros((2,8)).tolist())
    with pytest.raises(ValueError,match='registered horizon'):compare(a,a,p)

import numpy as np
import pytest
from benchmarks.export_grid_studio import line_flow,keep_samples


def test_three_phase_branch_power_current_and_loss_agree_with_SI():
    result=line_flow(np.array([230+0j]),np.array([220+0j]),.1,.1)
    assert result['p_kw'][0]==pytest.approx(34.5)
    assert result['q_kvar'][0]==pytest.approx(34.5)
    assert result['current_a'][0]==pytest.approx(50*2**.5)
    assert result['loss_kw'][0]==pytest.approx(1.5)


def test_reverse_power_has_reverse_sign_but_passive_losses_remain_positive():
    result=line_flow(np.array([220+0j]),np.array([230+0j]),.1,.1)
    assert result['p_kw'][0]<0
    assert result['loss_kw'][0]==pytest.approx(1.5)


def test_decimation_preserves_event_sides_spikes_and_endpoints():
    t=[i*.0005 for i in range(2001)];sides=['regular']*len(t);sides[0]='initial'
    t[1001]=t[1000];sides[1000:1002]=['pre_event','post_event']
    y=[0.]*len(t);y[177]=1000;y[178]=-1500
    report=dict(time_s=t,sample_side=sides,channels={'m.current_pu':y})
    keep=keep_samples(report)
    assert {0,len(t)-1,1000,1001,177,178}<=set(keep)
    assert keep==sorted(set(keep))
    assert len(keep)<len(t)

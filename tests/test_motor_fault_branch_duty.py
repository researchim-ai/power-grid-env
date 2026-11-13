import pytest
from benchmarks.motor_fault_branch_duty import integrate


def test_exact_square_linear_and_event_jump():
    # I rises 0->2 over 1s, then drops instantly to zero.
    assert integrate([0,1,1,2],[0,2,0,0],0,2)['i2t_a2s']==pytest.approx(4/3)


def test_event_endpoint_side_not_smeared():
    assert integrate([0,1,1,2,2,3],[0,0,3,3,0,0],1,2)['i2t_a2s']==9


@pytest.mark.parametrize('times,values,start,end',[
 ([0,2],[1],0,2),([0,2],[1,1],.1,2),([0,2],[1,-1],0,2),
 ([0,2,1],[1,1,1],0,2),([0,2],[1,float('nan')],0,2),([0,2],[1,1],2,0)])
def test_bad_trace(times,values,start,end):
    with pytest.raises(ValueError):integrate(times,values,start,end)

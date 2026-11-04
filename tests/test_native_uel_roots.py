"""Continuous root vector coverage, native-only operands and legacy separation."""
import numpy as np
import pytest
from benchmarks.native_uel_roots import indicator_names,ATOMIC_INDICATORS
from benchmarks.mnlex2_model_exchange import NativeUEL
from power_grid_env.generator_underexcitation import UnderexcitationLimiter


@pytest.fixture(params=['continuous_atoms','compound_and_atoms'])
def block(request):
    b=NativeUEL.__new__(NativeUEL);b.nx=2;b.native_nz=2;b.root_policy=request.param
    b.root_indicator_names=indicator_names(b.root_policy,b.native_nz);b.nz=len(b.root_indicator_names);b._native_indicator_buffer=np.empty(2)
    b.native_state_names=['memory','limiter.simpleLagLim.state'];b.p=UnderexcitationLimiter(generator_id='g')
    class FMU:
        raw=-.2;rate=.3;signs=[-1.,1.];calls=0
        def getEventIndicators(self,p,n):self.calls+=1;np.ctypeslib.as_array(p,(n,))[:]=self.signs
        def getContinuousStates(self,p,n):np.ctypeslib.as_array(p,(n,))[:]=[0.,self.raw]
        def getDerivatives(self,p,n):np.ctypeslib.as_array(p,(n,))[:]=[0.,self.rate]
    b.f=FMU();return b


def evaluate(b):
    v=np.full(b.nz,float('nan'));b.event_indicators(v);assert np.isfinite(v).all();return v


def test_boolean_compound_jumps_cannot_contaminate_continuous_root_vector(block,monkeypatch):
    from power_grid_env import generator_underexcitation
    def forbidden(*args):raise AssertionError('production RHS used by native root vector')
    monkeypatch.setattr(generator_underexcitation,'mnlex2_rates',forbidden)
    a=evaluate(block);block.f.signs=[1.,-1.];b=evaluate(block)
    assert block.f.calls==2
    assert np.array_equal(a[-4:],b[-4:])
    if block.root_policy=='continuous_atoms':assert np.array_equal(a,b) and len(b)==4
    else:assert np.array_equal(b[:2]-a[:2],[2.,-2.]) and len(b)==6


@pytest.mark.parametrize('index',range(4))
def test_every_bound_and_inward_rate_surface_has_an_explicit_zero(block,index):
    eps=64*np.finfo(float).eps
    if index==0:block.f.raw=0.
    elif index==1:block.f.raw=block.p.MEL_MAX
    elif index==2:block.f.rate=eps
    else:block.f.rate=-eps
    assert evaluate(block)[-4+index]==0.
    assert block.root_indicator_names[-4+index]==ATOMIC_INDICATORS[index]


def test_bound_root_is_linear_and_not_boolean_near_crossing(block):
    delta=1e-8;block.f.raw=block.p.MEL_MAX-delta;left=evaluate(block)[-3]
    block.f.raw=block.p.MEL_MAX+delta;right=evaluate(block)[-3]
    assert left<0<right and right-left==pytest.approx(2*delta,rel=1e-8)


def test_invalid_vector_size_is_rejected_before_calling_FMI(block):
    with pytest.raises(ValueError,match='size'):block.event_indicators(np.empty(block.nz+1))
    assert block.f.calls==0


def test_nonfinite_native_indicator_is_not_silently_hidden(block):
    block.f.signs=[float('nan'),-1.]
    with pytest.raises(ValueError,match='nonfinite'):evaluate(block)


@pytest.mark.parametrize('policy,count',[('unknown',2),('continuous_atoms',-1),('continuous_atoms',True)])
def test_invalid_root_contract_is_rejected(policy,count):
    with pytest.raises(ValueError):indicator_names(policy,count)


def test_invalid_constructor_policy_creates_no_artifacts(tmp_path):
    p=UnderexcitationLimiter(generator_id='g')
    with pytest.raises(ValueError,match='root policy'):NativeUEL(p,.1,.1,1.,tmp_path/'out',tmp_path/'missing',1.,root_policy='unknown')
    assert not (tmp_path/'out').exists()

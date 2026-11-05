"""Explicit, bounded below-clock ties; untouched legacy semantics."""
import copy
import numpy as np
import pytest
from benchmarks.oel2c_batch_validation import case
from benchmarks.oel2c_jacobian_validation import MULTI,case as legacy
from tests.test_esst1a_jacobian import ready,difference
from power_grid_env.oel2c_events import coalesce_below_clocks,POLICY
from power_grid_env.oel2c_jacobian import callback,KIND


@pytest.mark.parametrize('name',MULTI)
def test_optin_route_and_full_continuous_derivatives(name):
    m,x=ready(case(name));assert m.integration_jacobian().jacobian_kind==KIND
    for _,(a,p,k) in m.oel2c.entries.items():x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<4e-5
    old,z=ready(legacy(name));assert old.integration_jacobian() is None


def near_clock():
    m,x=ready(case('cold__two_limiters'));eps=64*np.finfo(float).eps
    for i,(a,p,k) in m.oel2c.entries.items():
        x[a+2]=p.KAct*x[a]+p.IThOffPu+eps
        k.context.update(below=False,thermal_high=True,thermal_low=False,reference_bound=0)
    return m,x


def test_requires_localized_seed():
    m,x=near_clock();assert coalesce_below_clocks(m.oel2c,set(),x)==set()


@pytest.mark.parametrize('distance',[1e-10,1e-8,1e-4])
def test_nearby_but_not_roundoff_tie_does_not_join(distance):
    m,x=near_clock();ids=list(m.oel2c.entries);a,_,_=m.oel2c.entries[ids[1]];x[a+2]+=distance
    seed={('oel2c',ids[0],'branch_below')};assert coalesce_below_clocks(m.oel2c,seed,x)==seed


def test_wrong_crossing_direction_does_not_join():
    m,x=near_clock();ids=list(m.oel2c.entries);_,p,_=m.oel2c.entries[ids[1]];p.Kru=-.3
    seed={('oel2c',ids[0],'branch_below')};assert coalesce_below_clocks(m.oel2c,seed,x)==seed


def test_other_guard_cannot_seed_batch():
    m,x=near_clock();seed={('oel2c',0,'branch_above')};assert coalesce_below_clocks(m.oel2c,seed,x)==seed


def test_clock_updates_commute_without_continuous_resets():
    m,x=near_clock();ids=list(m.oel2c.entries);roots=coalesce_below_clocks(m.oel2c,{('oel2c',ids[0],'branch_below')},x)
    assert len(roots)==2
    kernels=[copy.deepcopy(m.oel2c.entries[i][2]) for i in ids];states=[x[m.oel2c.entries[i][0]:m.oel2c.entries[i][0]+8].copy() for i in ids]
    outputs=[]
    for order in [range(2),reversed(range(2))]:
        ks=copy.deepcopy(kernels);xs=copy.deepcopy(states)
        for j in order:ks[j].apply({'branch_below'},.2,xs[j],[])
        assert all(np.array_equal(a,b) for a,b in zip(states,xs))
        outputs.append([k.context for k in ks])
    assert outputs[0]==outputs[1]


def test_atomic_log_order_independent_of_passport_order():
    m,x=near_clock();ids=list(m.oel2c.entries);saved=x.copy();bank=m.oel2c
    clone=copy.deepcopy([(i,a,p,k.context) for i,(a,p,k) in bank.entries.items()]);logs=[]
    bank.apply({('oel2c',ids[0],'branch_below')},.2,x,logs)
    first=[(e['equipment_id'],e['transition'],e['value']) for e in logs]
    for i,a,p,context in clone:bank.entries[i][2].context=context
    bank.entries=dict(reversed(list(bank.entries.items())));logs=[];bank.apply({('oel2c',ids[1],'branch_below')},.2,x,logs)
    assert first==[(e['equipment_id'],e['transition'],e['value']) for e in logs]
    assert np.array_equal(x,saved)


def test_policy_not_exported_as_native_Modelica_parameter():
    m,x=near_clock()
    assert all('coincidence_policy' not in p.native_parameters() for _,p,_ in m.oel2c.entries.values())


def test_mixed_policy_does_not_join_or_enable_analytic():
    m,x=near_clock();ids=list(m.oel2c.entries);m.oel2c.entries[ids[1]][1].coincidence_policy='legacy'
    seed={('oel2c',ids[0],'branch_below')}
    assert coalesce_below_clocks(m.oel2c,seed,x)==seed and callback(m) is None


def test_other_context_branch_does_not_join():
    m,x=near_clock();ids=list(m.oel2c.entries);m.oel2c.entries[ids[1]][2].context['below']=True
    seed={('oel2c',ids[0],'branch_below')};assert coalesce_below_clocks(m.oel2c,seed,x)==seed


def test_shallow_roundoff_close_guard_is_not_a_coincident_clock(monkeypatch):
    m,x=near_clock();ids=list(m.oel2c.entries);i=ids[1];a,p,k=m.oel2c.entries[i]
    # Exact sensor equilibrium makes the positive below-guard rate tiny.
    v=m.electrical(x)[0];inputs=m.oel2c.inputs(x,m.generator_currents(x,v));x[a]=p.KScale*inputs[i]
    eps=64*np.finfo(float).eps;x[a+2]=p.KAct*x[a]+p.IThOffPu+eps+eps/4
    p.Kru=1e-15
    seed={('oel2c',ids[0],'branch_below')}
    assert coalesce_below_clocks(m.oel2c,seed,x,.2)==seed

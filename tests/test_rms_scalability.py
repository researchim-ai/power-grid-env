"""Independent all-node port checks and explicit opt-in network-size budgets."""
import copy
import pytest
from benchmarks.rms_scalability import example,check
from power_grid_env.study_spec import run_study
from power_grid_env.topology import compile_topology


@pytest.mark.parametrize('coupled',[False,True])
def test_full_sparse_network_voltage_matches_closed_form(coupled):
    s=example(16,coupled);r=run_study(s);assert r['completed'],r
    assert check(s,r)['passed']
    altered=copy.deepcopy(r);bus=compile_topology(s.network).node_to_bus['n7']
    altered['channels'][bus+'.v'][-1]*=1.01
    assert not check(s,altered)['passed']


@pytest.mark.parametrize('coupled',[False,True])
def test_network_power_base_does_not_change_physical_oracle(coupled):
    s=example(16,coupled);s.network.s_base_va*=7.
    r=run_study(s);assert r['completed'],r
    assert check(s,r)['passed']


def test_network_size_budget_is_explicit_and_cannot_be_silently_ignored():
    s=example(32);s.induction.runtime_limits.maximum_network_buses=16
    r=run_study(s);assert not r['completed'] and r['reason']=='induction_scope'
    s.induction.runtime_limits.maximum_network_buses=64
    r=run_study(s);assert r['completed'] and check(s,r)['passed']

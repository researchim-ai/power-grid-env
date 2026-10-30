"""A private native context is reused without carrying electrical state across solves."""
import pytest
from power_grid_env import backend_opendss as backend
from power_grid_env.build import NetBuilder


def make(name, power, frequency=50.):
    b=NetBuilder(name, name, 10., s_base_va=1e6)
    b.node('src'); b.node('ld'); b.source('src', .05, .2)
    b.line_z1('src', 'ld', .2+.4j, line_id='line')
    b.load('ld', power, .2*power, load_id='load')
    n=b.build(); n.frequency_hz=frequency
    return n


def test_reusing_native_context_clears_network_and_frequency_between_runs():
    light=make('light', 100000.)
    heavy=make('heavy', 2000000., 60.)
    first=backend.solve_network(light)
    context=backend._get_session()
    for _ in range(20):
        other=backend.solve_network(heavy)
        assert other.status=='converged'
    final=backend.solve_network(light)
    assert backend._get_session() is context
    assert final.status==first.status=='converged'
    assert [v.complex for v in final.voltages]==pytest.approx([v.complex for v in first.voltages], rel=1e-10)
    assert max(abs(v.complex) for v in other.voltages) > 0
    assert any(abs(a.complex-b.complex)>1 for a,b in zip(other.voltages,first.voltages))

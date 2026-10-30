"""Parallel requests never share a worker's protocol or consume an unbounded iterable."""
import pytest
from power_grid_env.worker_pool import SolverPool
from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder


def network(p):
    b = NetBuilder(str(p), str(p), 10., s_base_va=1e6)
    b.node('src'); b.node('ld'); b.source('src', .05, .2)
    b.line_z1('src', 'ld', .2+.4j, line_id='line')
    b.load('ld', p, p*.2, load_id='load')
    return b.build()


def test_ordered_bounded_parallel_batch_recovers_worker_and_matches_direct():
    nets = [network(p) for p in (100000., 200000., 500000., 800000., 1200000.)]
    expected = [solve_network(n) for n in nets]
    consumed = []
    def inputs():
        for i, n in enumerate(nets):
            consumed.append(i)
            yield n
    with SolverPool(2) as pool:
        pool.workers[0]._kill()
        stream = pool.solve_many(inputs())
        first = next(stream)
        assert consumed == [0, 1]
        actual = [first, *stream]
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            assert a.status == e.status == 'converged'
            assert [v.complex for v in a.voltages] == pytest.approx([v.complex for v in e.voltages], rel=1e-8)
        pool.workers[0]._kill()
        with pytest.raises(RuntimeError):
            list(pool.solve_many(nets[:1], retries=0))
    with pytest.raises(RuntimeError, match='closed'):
        pool.solve(0, nets[0])

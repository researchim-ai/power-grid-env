"""Bounded parallel independent solves, one serialized DSS context per process."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from typing import Iterable, Iterator

from power_grid_env.backend_process import ProcessBackend
from power_grid_env.model import Network
from power_grid_env.results import SolveOptions, SolveResult


class SolverPool:
    def __init__(self, size: int = 2, timeout_s: float = 60.0) -> None:
        if size < 1 or timeout_s <= 0:
            raise ValueError("в пуле есть хотя бы один процесс, ожидание положительно")
        self.timeout_s = timeout_s
        self.workers: list[ProcessBackend] = []
        self._locks = [threading.Lock() for _ in range(size)]
        self._closed = False
        try:
            for _ in range(size):
                self.workers.append(ProcessBackend(timeout_s))
        except BaseException:
            for worker in self.workers:
                worker.close()
            raise

    def _check(self, index: int) -> None:
        if self._closed:
            raise RuntimeError("pool is closed")
        if index < 0 or index >= len(self.workers):
            raise ValueError("нет процесса с таким номером")

    def solve(self, index: int, network: Network, options: SolveOptions | None = None) -> SolveResult:
        self._check(index)
        with self._locks[index]:
            self._check(index)
            return self.workers[index].solve(network, options)

    def replace(self, index: int) -> None:
        self._check(index)
        with self._locks[index]:
            self._check(index)
            self.workers[index].close()
            self.workers[index] = ProcessBackend(self.timeout_s)

    def _retry(self, index, network, options, retries):
        # Hold the same lock throughout replacement, preserving protocol framing
        # even when external callers also address this worker.
        self._check(index)
        with self._locks[index]:
            self._check(index)
            for attempt in range(retries + 1):
                try:
                    return self.workers[index].solve(network, options)
                except (TimeoutError, RuntimeError):
                    if attempt == retries:
                        raise
                    self.workers[index].close()
                    self.workers[index] = ProcessBackend(self.timeout_s)

    def solve_many(self, networks: Iterable[Network], options: SolveOptions | None = None,
                   *, retries: int = 1) -> Iterator[SolveResult]:
        """Yield in input order, retaining at most size requests/results per batch.

        Retries apply only to independent idempotent network solves. Numerical
        nonconvergence is a result, not a reason to silently substitute/retry.
        Running requests retain their individual backend deadlines.
        """
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        self._check(0)
        source = iter(networks)
        with ThreadPoolExecutor(max_workers=len(self.workers)) as executor:
            while batch := list(islice(source, len(self.workers))):
                futures = [executor.submit(self._retry, i, network, options, retries)
                           for i, network in enumerate(batch)]
                for future in futures:
                    yield future.result()

    def close(self) -> None:
        for index, worker in enumerate(self.workers):
            with self._locks[index]:
                worker.close()
        self._closed = True

    def __enter__(self):
        self._check(0)
        return self

    def __exit__(self, *exc):
        self.close()

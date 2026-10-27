"""Долгоживущий процесс решателя. Один контекст OpenDSS на экземпляр."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

from power_grid_env.ipc import FrameTimeout, ProtocolError, kill_process_group, parse_object, read_frame, write_frame
from power_grid_env.model import Network
from power_grid_env.results import SolveOptions, SolveResult


class ProcessBackend:
    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self.process = subprocess.Popen(
            [sys.executable, "-m", "power_grid_env.backend_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

    def solve(self, network: Network, options: SolveOptions | None = None) -> SolveResult:
        payload = {
            "op": "solve",
            "network": network.model_dump(mode="json"),
            "options": (options or SolveOptions()).model_dump(mode="json"),
        }
        assert self.process.stdin is not None and self.process.stdout is not None
        deadline = time.monotonic() + self.timeout_s
        try:
            write_frame(self.process.stdin, payload, deadline, 64_000_000)
            raw, _stderr = read_frame(self.process.stdout, self.process.stderr, deadline, 64_000_000)
            data = parse_object(raw)
        except FrameTimeout as exc:
            self._kill()
            raise TimeoutError(f"backend не ответил за {self.timeout_s} с") from exc
        except (BrokenPipeError, ProtocolError) as exc:
            self._kill()
            raise RuntimeError(f"процесс backend завершился: {exc}") from exc
        return SolveResult.model_validate(data)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            assert self.process.stdin is not None
            write_frame(self.process.stdin, {"op": "close"}, time.monotonic() + 2.0, 4096)
        except (BrokenPipeError, FrameTimeout, OSError):
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill()

    def _kill(self) -> None:
        kill_process_group(self.process)

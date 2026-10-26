"""Долгоживущий процесс решателя. Один контекст OpenDSS на экземпляр."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
from typing import Any

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
            text=True,
            start_new_session=True,
        )

    def solve(self, network: Network, options: SolveOptions | None = None) -> SolveResult:
        payload = {
            "op": "solve",
            "network": network.model_dump(mode="json"),
            "options": (options or SolveOptions()).model_dump(mode="json"),
        }
        assert self.process.stdin is not None and self.process.stdout is not None
        self._drain_stderr()
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], self.timeout_s)
        if not ready:
            self._kill()
            raise TimeoutError(f"backend не ответил за {self.timeout_s} с")
        line = self.process.stdout.readline()
        if not line:
            error = ""
            if self.process.stderr is not None:
                error = self.process.stderr.read()
            raise RuntimeError(f"процесс backend завершился: {error[:500]}")
        data: dict[str, Any] = json.loads(line)
        return SolveResult.model_validate(data)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps({"op": "close"}) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill()

    def _drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        descriptor = self.process.stderr.fileno()
        os.set_blocking(descriptor, False)
        try:
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    break
        finally:
            os.set_blocking(descriptor, True)

    def _kill(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            self.process.kill()

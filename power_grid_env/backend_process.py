"""Долгоживущий процесс решателя. Один контекст OpenDSS на экземпляр."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from power_grid_env.model import Network
from power_grid_env.results import SolveOptions, SolveResult


class ProcessBackend:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "power_grid_env.backend_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def solve(self, network: Network, options: SolveOptions | None = None) -> SolveResult:
        payload = {
            "op": "solve",
            "network": network.model_dump(mode="json"),
            "options": (options or SolveOptions()).model_dump(mode="json"),
        }
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
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
            self.process.kill()

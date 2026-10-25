"""Процесс одного экземпляра backend. Родительский процесс не делит контекст OpenDSS."""

from __future__ import annotations

import json
import sys

from power_grid_env.backend_opendss import solve_network
from power_grid_env.model import Network
from power_grid_env.results import SolveOptions, SolveResult


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if request.get("op") == "close":
            break
        try:
            network = Network.model_validate(request["network"])
            options = SolveOptions.model_validate(request.get("options") or {})
            result = solve_network(network, options)
            sys.stdout.write(result.model_dump_json() + "\n")
        except Exception as exc:
            failed = SolveResult(
                status="invalid_model",
                converged=False,
                iterations=0,
                algorithm="not_run",
                tolerance=1e-10,
                backend_name="opendss-dss-extensions",
                backend_version="worker",
                warnings=[str(exc)],
                diagnostics={"worker_error": str(exc), "collapse": False},
            )
            sys.stdout.write(failed.model_dump_json() + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

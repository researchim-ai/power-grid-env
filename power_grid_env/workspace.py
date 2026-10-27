"""Локальное исследование. CLI и HTTP вызывают одни и те же функции.

Слушает только 127.0.0.1. Вид контроллера не получает привилегированный снимок,
скрытый импеданс повреждения и canary.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from power_grid_env.importer import import_network
from power_grid_env.model import Network
from power_grid_env.preflight import preflight
from power_grid_env.report import write_json
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model
from power_grid_env.viewer import write_viewer, viewer_bundle

_HIDDEN = {"privileged", "hidden_canary", "fault_r_ohm", "model_mismatch_scale", "script"}


class StudyWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def import_source(self, source: Path, sidecar: Path | None = None) -> dict[str, Any]:
        network, report = import_network(source, sidecar)
        write_json(self.root / "import_report.json", report)
        page = html_errors(report.get("errors") or [])
        (self.root / "import_report.html").write_text(page, encoding="utf-8")
        if network is None:
            self._write_job({"status": "import_rejected", "ok": False})
            return {"ok": False, "errors": report.get("errors") or []}
        (self.root / "model.json").write_text(network.model_dump_json(indent=2), encoding="utf-8")
        checked = preflight(network, task="power_flow", solve_initial=False)
        write_json(self.root / "preflight.json", checked)
        self._write_job({"status": "imported", "ok": checked["ok"], "preflight_errors": checked["errors"]})
        return {"ok": checked["ok"], "errors": checked["errors"], "network_id": network.id}

    def load_network(self) -> Network:
        return Network.model_validate_json((self.root / "model.json").read_text(encoding="utf-8"))

    def run(self, *, steps: int = 1, horizon_steps: int | None = None, hidden_canary: str | None = None) -> dict[str, Any]:
        if steps < 1:
            raise ValueError("steps должен быть положительным")
        horizon_steps = steps if horizon_steps is None else horizon_steps
        if horizon_steps < steps:
            raise ValueError("horizon_steps короче запрошенного прогона")
        network = self.load_network()
        scenario = Scenario(
            id=network.id,
            horizon_s=60.0 * horizon_steps,
            control_interval_s=60.0,
            settle_s=10_000.0,
            hidden_canary=hidden_canary,
            fault_disclosed=False,
        )
        engine = Engine(network, scenario)
        engine.reset()
        taken = 0
        for _ in range(steps):
            if self.cancel_requested():
                return self._finish(engine, scenario, taken, status="cancelled")
            engine.step_control({"kind": "hold"})
            taken += 1
        return self._finish(engine, scenario, taken, status="completed")

    def resume(self) -> dict[str, Any]:
        network = self.load_network()
        raw = json.loads((self.root / "snapshot.json").read_text(encoding="utf-8"))
        scenario = Scenario.model_validate(raw["scenario"])
        engine = Engine(network, scenario)
        engine.restore(raw["engine"])
        if engine.truncated or engine.terminated:
            return self._finish(engine, scenario, 0, status="horizon_reached")
        if self.cancel_requested():
            return self._finish(engine, scenario, 0, status="cancelled")
        engine.step_control({"kind": "hold"})
        return self._finish(engine, scenario, 1, status="resumed")

    def request_cancel(self) -> None:
        (self.root / "cancel").write_text("1", encoding="utf-8")

    def cancel_requested(self) -> bool:
        return (self.root / "cancel").exists()

    def public_view(self) -> dict[str, Any]:
        payload = json.loads((self.root / "public.json").read_text(encoding="utf-8"))
        return _without_hidden(payload)

    def job(self) -> dict[str, Any]:
        path = self.root / "job.json"
        if not path.exists():
            return {"status": "absent"}
        return json.loads(path.read_text(encoding="utf-8"))

    def _finish(self, engine: Engine, scenario: Scenario, taken: int, *, status: str) -> dict[str, Any]:
        snapshot = {"scenario": scenario.model_dump(mode="json"), "engine": engine.snapshot()}
        write_json(self.root / "snapshot.json", snapshot)
        public = _public_payload(engine, scenario)
        write_json(self.root / "public.json", public)
        trace = engine.trace or [
            {
                "time_s": engine.time_s,
                "action": {"kind": "hold"},
                "acceptance": {"accepted": True},
                "origin": "controller",
                "operations": [],
                "solve_status": None if engine.last_result is None else engine.last_result.status,
                "violations": [],
                "observation": engine.observation(),
                "privileged": {"source": "privileged", "voltages": [], "currents": []},
            }
        ]
        bundle = viewer_bundle(engine.net.model_dump(mode="json"), trace, scenario.v_min_pu, scenario.v_max_pu)
        write_viewer(self.root / "viewer.html", bundle)
        record = {
            "status": status,
            "ok": status in {"completed", "resumed"} and (engine.last_result is None or engine.last_result.status == "converged"),
            "steps": taken,
            "time_s": engine.time_s,
            "solve_status": None if engine.last_result is None else engine.last_result.status,
        }
        self._write_job(record)
        return record

    def _write_job(self, record: dict[str, Any]) -> None:
        write_json(self.root / "job.json", record)


def html_errors(errors: list[str]) -> str:
    items = "".join(f"<li>{html.escape(item)}</li>" for item in errors)
    return f"<!DOCTYPE html><html lang=\"ru\"><meta charset=\"utf-8\"><ul>{items}</ul></html>"


def _public_payload(engine: Engine, scenario: Scenario) -> dict[str, Any]:
    steps = []
    for step in engine.trace:
        public_step = {key: value for key, value in step.items() if key not in _HIDDEN}
        public_step["observation"] = step.get("observation")
        steps.append(_without_hidden(public_step))
    exposed = agent_model(engine.base_network, scenario)
    return {
        "scenario": scenario.public_config(exposed.model_dump(mode="json")),
        "steps": steps,
        "time_s": engine.time_s,
    }


def _without_hidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_hidden(item) for key, item in value.items() if key not in _HIDDEN}
    if isinstance(value, list):
        return [_without_hidden(item) for item in value]
    return value


def serve(workspace: StudyWorkspace, *, port: int = 0) -> ThreadingHTTPServer:
    """Только локальная петля. Адрес 0.0.0.0 здесь не принимается."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                body = {"ok": True, "bind": "127.0.0.1"}
            elif path == "/public":
                try:
                    body = workspace.public_view()
                except FileNotFoundError:
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server

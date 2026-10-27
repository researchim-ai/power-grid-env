"""Исследование, каналы Gym и честный конверт масштаба."""

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import numpy as np

from power_grid_env.build import NetBuilder
from power_grid_env.gym_env import RestorationEnv
from power_grid_env.scenarios import Scenario
from power_grid_env.workspace import StudyWorkspace, html_errors, serve


def _network(meters: int = 1):
    builder = NetBuilder("ws", "ws", 10.0)
    for index in range(meters):
        builder.node(f"n{index}")
        if index:
            builder.line_z1(f"n{index - 1}", f"n{index}", 0.05 + 0.1j, line_id=f"l{index}")
        builder.meter(f"n{index}", meter_id=f"m{index}")
    builder.source("n0", 0.01, 0.05)
    builder.load(f"n{meters - 1}", 50_000.0, 10_000.0)
    return builder.build()


def test_study_reload_cancel_and_public_view_hide_privileged_data(tmp_path: Path):
    source = tmp_path / "model.json"
    source.write_text(_network().model_dump_json(), encoding="utf-8")
    workspace = StudyWorkspace(tmp_path / "study")
    imported = workspace.import_source(source)
    assert imported["ok"]
    finished = workspace.run(steps=1, horizon_steps=2, hidden_canary="CANARY-secret")
    assert finished["ok"]
    assert finished["time_s"] == 60.0
    public_text = (tmp_path / "study" / "public.json").read_text(encoding="utf-8")
    assert "CANARY-secret" not in public_text
    assert "privileged" not in public_text
    assert "fault_r_ohm" not in public_text
    reloaded = StudyWorkspace(tmp_path / "study")
    assert reloaded.job()["time_s"] == finished["time_s"]
    resumed = reloaded.resume()
    assert resumed["status"] == "resumed"
    assert resumed["time_s"] == 120.0
    page = html_errors(["<script>alert(1)</script>"])
    assert "<script>" not in page
    assert "&lt;script&gt;" in page

    cancelled_dir = StudyWorkspace(tmp_path / "cancelled")
    assert cancelled_dir.import_source(source)["ok"]
    cancelled_dir.request_cancel()
    cancelled = cancelled_dir.run(steps=3)
    assert cancelled["status"] == "cancelled"
    assert cancelled["steps"] == 0

    server = serve(workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/public") as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["time_s"] == 120.0
        assert "CANARY-secret" not in json.dumps(body)
        with urlopen(f"http://127.0.0.1:{port}/health") as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["bind"] == "127.0.0.1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_gym_keeps_every_meter_channel():
    network = _network(meters=3)
    environment = RestorationEnv(network, Scenario(id="gym", horizon_s=60, control_interval_s=60, settle_s=10_000))
    observation, _info = environment.reset(seed=1)
    assert observation["voltage_pu"].shape == (9,)
    assert observation["voltage_valid"].shape == (9,)
    assert int(np.sum(observation["voltage_valid"])) == 9
    assert environment.channels[0]["meter_id"] == "m0"
    assert environment.channels[-1] == {"meter_id": "m2", "phase": "C"}


def test_envelope_does_not_claim_unmeasured_sizes():
    from power_grid_env.benchmarks import measure_envelope

    payload = measure_envelope(sizes=(3,), probe_10000=False)
    assert payload["size_claim"] is False
    assert payload["workloads"][0]["status"] == "converged"
    assert payload["workloads"][0]["claimed_as_supported_city"] is False
    assert payload["buses_10000"]["status"] == "not_probed"
    assert payload["horizons_days"] == {"7": "not_measured", "30": "not_measured"}


def test_n1_keeps_every_outage_and_singular_estimator_does_not_succeed():
    from power_grid_env.analysis import n1_line_outages, wls_dc

    network = _network()
    reports = n1_line_outages(network)
    assert [item["outage"] for item in reports] == [line.id for line in network.lines]
    singular = wls_dc(np.array([1.0, 1.1]), np.array([[1.0, 0.0], [1.0, 0.0]]), np.array([0.01, 0.01]))
    assert singular["observable"] is False
    assert singular["estimate"] is None


def test_baseline_report_does_not_claim_superiority():
    from benchmarks.external.baselines import baseline_report

    payload = baseline_report()
    assert payload["competitive_superiority_proven"] is False
    by_id = {item["id"]: item["comparison_run"] for item in payload["candidates"]}
    assert by_id["direct-dss"] == "conversion_consistency"
    assert by_id["andes"] == "not_performed"
    assert by_id["grid2op"] == "not_performed"


def test_qualification_bundle_is_not_a_release(tmp_path: Path):
    from power_grid_env.cli import main

    assert main(["qualify", "--out", str(tmp_path)]) == 0
    payload = json.loads((tmp_path / "qualification.json").read_text(encoding="utf-8"))
    assert payload["released"] is False
    assert payload["validated_for_declared_scope"] is False
    assert any("AG-14" in item for item in payload["open_limits"])
    assert "simulation.py" in payload["package_py_sha256"]

"""Engineering results, durable continuation and fail-closed evidence."""
import hashlib
import json
import math

import pytest

from power_grid_env.build import NetBuilder
from power_grid_env.cli import main
from power_grid_env.evidence import load_evidence, package_fingerprint, qualify_profile
from power_grid_env.scenarios import Scenario
from power_grid_env.study_spec import StudySpec, run_study, run_study_document


def spec():
    b = NetBuilder("durable", "Durable", 10.0, s_base_va=1e6)
    b.node("src")
    b.node("ld")
    b.source("src", .05, .2)
    b.line_z1("src", "ld", .2+.4j, line_id="line")
    b.load("ld", 100000., 20000., critical=True, load_id="load")
    return StudySpec(id="durable", study="qsts", backend="opendss", network=b.build(),
                     scenario=Scenario(id="durable", horizon_s=1800, control_interval_s=900,
                                       load_profile={"load": [[0, 1., 1.], [900, .5, .7]]}))


def test_streamed_resume_preserves_full_physics_and_discards_uncommitted_rows(tmp_path):
    s = spec()
    full = run_study(s, out=tmp_path/"full")
    part = run_study(s, out=tmp_path/"part", max_steps=1)
    assert part["status"] == "paused"
    with (tmp_path/"part/intervals.jsonl").open("ab") as f:
        f.write(b'{"uncommitted": true}\n')
    resumed = run_study(s, out=tmp_path/"part", resume=True)
    assert resumed["completed"]
    assert resumed["intervals"] == 2
    assert resumed["journal_sha256"] == full["journal_sha256"]
    assert resumed["integrals"] == pytest.approx(full["integrals"])
    assert resumed["integrals"]["critical_demand_wh"] == pytest.approx(37500.)
    assert resumed["last_result"] == full["last_result"]
    assert resumed["retained_history_steps"] == 1
    assert resumed["retained_trace_steps"] <= 2
    rows = [json.loads(row) for row in (tmp_path/"part/intervals.jsonl").read_text().splitlines()]
    assert all(row["result"]["flows"] and row["result"]["voltages"] for row in rows)
    assert [row["interval"] for row in rows] == [0, 1]
    with pytest.raises(FileExistsError):
        run_study(s, out=tmp_path/"part")


def test_checkpoint_journal_identity_and_single_writer_are_enforced(tmp_path):
    s = spec()
    out = tmp_path/"run"
    run_study(s, out=out, max_steps=1)
    changed = s.model_copy(deep=True)
    changed.network.loads[0].p_w["A"] *= 2
    with pytest.raises(ValueError, match="another study"):
        run_study(changed, out=out, resume=True)
    journal = out/"intervals.jsonl"
    raw = journal.read_bytes()
    journal.write_bytes(b"!" + raw[1:])
    with pytest.raises(ValueError, match="checksum"):
        run_study(s, out=out, resume=True)
    journal.write_bytes(raw)
    saved = json.loads((out/"checkpoint.json").read_text())
    saved["engine"]["time_s"] += 1
    (out/"checkpoint.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="checksum"):
        run_study(s, out=out, resume=True)
    (out/".lock").write_text("another writer")
    with pytest.raises(FileExistsError):
        run_study(s, out=out, resume=True)
    assert (out/".lock").exists()


def test_cancel_resume_and_cli_exit_codes(tmp_path):
    s = spec()
    cancelled = run_study(s, out=tmp_path/"cancel", cancelled=lambda: True)
    assert cancelled["status"] == "cancelled" and cancelled["intervals"] == 0
    assert run_study(s, out=tmp_path/"cancel", resume=True)["completed"]
    document = tmp_path/"study.json"
    document.write_text(s.model_dump_json())
    args = ["run-study", str(document), "--out", str(tmp_path/"cli")]
    assert main([*args, "--max-steps", "1"]) == 3
    assert main([*args, "--resume"]) == 0
    assert main(args) == 2
    assert json.loads((tmp_path/"cli/result.json").read_text())["completed"]


def test_full_pf_result_and_rejected_profile_before_solver():
    s = spec().model_copy(update={"study": "power_flow", "scenario": None})
    report = run_study(s)
    assert report["completed"]
    assert report["result"]["backend_version"]
    assert report["result"]["flows"] and report["result"]["voltages"]
    assert run_study_document([])["reason"] == "schema"
    bad = spec()
    bad.scenario.load_profile = {"missing-load": [[0., 1.]]}
    def forbidden(network):
        raise AssertionError("invalid profiles must not invoke solver")
    assert run_study(bad, forbidden, out="unused")["reason"] == "invalid_load_profile"
    with pytest.raises(ValueError):
        Scenario(id="nonfinite", horizon_s=math.inf)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1., True, None, "0"])
def test_nonfinite_negative_and_boolean_error_metrics_never_pass(value):
    record = ready()
    record["result"] = {"voltage_error_pu": value}
    report = qualify_profile("industrial-network-studies", [record])
    assert report["gates"][0]["status"] != "pass"


def ready():
    return {"id": "plant_pack", "network_class": "industrial_plant_110_10_0_4",
            "state": "qualified_for_profile", "code_sha256": package_fingerprint(),
            "result": {"voltage_error_pu": 1e-8}, "equipment": ["transformer"]}


def test_duplicate_failed_and_inherited_evidence_stays_closed():
    record = ready()
    assert qualify_profile("industrial-network-studies", [record, record])["gates"][0]["status"] != "pass"
    assert qualify_profile("industrial-network-studies", [{**record, "verdict": "fail"}])["gates"][0]["status"] == "fail"
    assert len(qualify_profile("industrial-dynamic-studies", [])["gates"]) == 4


def test_evidence_loader_binds_artifacts_and_rejects_tampering(tmp_path):
    record = ready()
    artifact = {**record, "verdict": "pass"}
    path = tmp_path/"artifact.json"
    path.write_text(json.dumps(artifact))
    record["result"] = {"voltage_error_pu": 0.}  # Index cannot replace artifact metrics.
    record["artifacts"] = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    index = tmp_path/"evidence.json"
    index.write_text(json.dumps([record]))
    assert load_evidence(index)[0]["result"] == artifact["result"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        load_evidence(index)
    assert main(["qualify", "--profile", "industrial-network-studies", "--evidence", str(index),
                 "--out", str(tmp_path/"qualification")]) == 1


def test_resume_preserves_delayed_controls_and_pending_measurements(tmp_path):
    b = NetBuilder('devices', 'Devices', 10., s_base_va=1e6)
    b.node('src'); b.node('ld'); b.source('src', .05, .2)
    b.line_z1('src', 'ld', 1.+2j, line_id='line')
    b.load('ld', 200000., 40000., model='constant_z', critical=True, load_id='load')
    b.shunt('ld', 400., shunt_id='cap')
    b.meter('ld', meter_id='meter', delay_s=15.)
    net = b.build(); net.shunts[0].steps=1; net.shunts[0].steps_in=0
    scenario = Scenario(id='devices', horizon_s=40., control_interval_s=10., measurement_period_s=5.,
                        capacitor_controls=[{'shunt_id': 'cap', 'measured_node_id': 'ld',
                        'close_below_pu': 1., 'open_above_pu': 1.05, 'delay_s': 15.}])
    s = StudySpec(id='devices', study='qsts', backend='opendss', network=net, scenario=scenario)
    full = run_study(s, out=tmp_path/'full')
    run_study(s, out=tmp_path/'resumed', max_steps=1)
    resumed = run_study(s, out=tmp_path/'resumed', resume=True)
    assert resumed['journal_sha256'] == full['journal_sha256']
    assert resumed['integrals'] == full['integrals']
    saved = json.loads((tmp_path/'resumed/checkpoint.json').read_text())
    assert saved['engine']['network']['shunts'][0]['steps_in'] == 1
    assert any(row['available_s'] > 40 for row in saved['engine']['measurements'])
    assert resumed['retained_measurements'] <= 5

"""Профиль поставки не открывается примером, а сохранённое исследование отклоняет чужой объект до расчёта."""

import json
import math

from power_grid_env.build import NetBuilder
from power_grid_env.cli import main
from power_grid_env.evidence import historical_claims, package_fingerprint, qualify_profile
from power_grid_env.study_spec import StudySpec, run_study, run_study_document
from power_grid_env.topology import compile_topology


def test_historical_regressions_do_not_release_a_profile(tmp_path):
    report = qualify_profile("industrial-network-studies")
    assert report["released"] is False
    assert report["verdict"] == "fail"
    assert {item["status"] for item in report["gates"]} == {"missing"}
    assert all(item["state"] == "regression_verified" for item in historical_claims())
    assert all(item["field_validated"] is False for item in historical_claims())
    assert {item["id"] for item in historical_claims()} >= {f"PM-{index:02d}" for index in range(12)}
    assert main(["qualify", "--profile", "industrial-network-studies", "--out", str(tmp_path)]) == 1
    saved = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert saved["released"] is False
    assert main(["qualify", "--profile", "unknown-profile", "--out", str(tmp_path)]) == 1


def test_stale_failed_and_small_records_stay_closed():
    fingerprint = package_fingerprint()
    plant = {
        "id": "plant_pack",
        "network_class": "industrial_plant_110_10_0_4",
        "state": "qualified_for_profile",
        "code_sha256": fingerprint,
        "result": {"voltage_error_pu": 1.0e-8},
        "equipment": ["transformer"],
    }
    small = {**plant, "network_class": "two_bus"}
    stale = {**plant, "code_sha256": "0" * 64}
    failed = {**plant, "result": {"voltage_error_pu": 1.0e-3}}
    unsupported = {**plant, "equipment": ["unsupported"]}
    assert _gate(qualify_profile("industrial-network-studies", [small]), "plant_pack") == "inconclusive"
    assert _gate(qualify_profile("industrial-network-studies", [stale]), "plant_pack") == "stale"
    assert _gate(qualify_profile("industrial-network-studies", [failed]), "plant_pack") == "fail"
    assert _gate(qualify_profile("industrial-network-studies", [unsupported]), "plant_pack") == "unsupported"
    complete = [
        plant,
        _ready("two_feeder_qsts", "two_feeders_15min", "missing_intervals", 0.0, fingerprint),
        _ready("clean_install", "other_machine", "install_gap", 0.0, fingerprint),
    ]
    opened = qualify_profile("industrial-network-studies", complete)
    assert opened["verdict"] == "pass"
    assert opened["released"] is True
    assert opened["small_case_promoted"] is False


def test_one_runner_solves_two_networks_and_rejects_before_calculation():
    light = _spec("light", 200_000.0)
    heavy = _spec("heavy", 2_000_000.0)
    first = run_study(light)
    second = run_study(heavy)
    assert first["solved"] is True and second["solved"] is True
    assert first["status"] == "converged" and second["status"] == "converged"
    assert abs(_receiving(light) - _receiving(heavy)) / _receiving(light) > 0.01

    def forbidden(_network):
        raise AssertionError("расчёт не должен стартовать")

    motor = light.model_copy(update={"requested_motors": ["double_cage"]})
    other = light.model_copy(update={"backend": "grid2op"})
    harmonic = light.model_copy(update={"study": "harmonic"})
    assert run_study(motor, forbidden)["reason"] == "unsupported_motor"
    assert run_study(other, forbidden)["reason"] == "unsupported_backend"
    assert run_study(harmonic, forbidden)["reason"] == "unsupported_study"
    document = {"id": "old", "kind": "power_flow", "backend": "opendss", "network": light.network.model_dump(mode="json")}
    migrated = run_study_document(document)
    assert migrated["solved"] is True
    from power_grid_env.study_spec import migrate_study

    restored = StudySpec.model_validate(StudySpec.model_validate(migrate_study(document)).model_dump())
    assert restored.study == "power_flow"
    assert restored.network.lines[0].r_ohm == light.network.lines[0].r_ohm
    broken = json.loads(json.dumps(document))
    broken["kind"] = "power_flow"
    broken["network"]["transformers"] = [{"id": "t", "vector_group": "Yy99", "windings": [], "x_hl_pct": 6.0, "load_loss_pct": 1.0, "no_load_loss_pct": 0.0, "imag_pct": 0.0, "provenance": light.network.provenance.model_dump(mode="json")}]
    rejected = run_study_document(broken, forbidden)
    assert rejected["solved"] is False
    assert rejected["reason"] == "schema"


def _gate(report: dict, claim: str) -> str:
    return next(item["status"] for item in report["gates"] if item["id"] == claim)


def _ready(claim: str, network_class: str, metric: str, value: float, fingerprint: str) -> dict:
    return {
        "id": claim,
        "network_class": network_class,
        "state": "qualified_for_profile",
        "code_sha256": fingerprint,
        "result": {metric: value},
        "equipment": ["declared"],
    }


def _spec(name: str, active_w: float) -> StudySpec:
    builder = NetBuilder(name, name, 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.4 + 1.2j, line_id="line")
    builder.load("ld", active_w, active_w * 0.2, load_id="load")
    return StudySpec(id=name, study="power_flow", backend="opendss", network=builder.build())


def _receiving(spec: StudySpec) -> float:
    from power_grid_env.backend_opendss import solve_network

    solved = solve_network(spec.network)
    bus = compile_topology(spec.network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(voltage) / (10_000.0 / math.sqrt(3.0))

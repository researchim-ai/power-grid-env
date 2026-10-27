"""Внешний бенчмарк: пороги не ослабляются, неполный перенос не получает pass."""

from pathlib import Path

import pytest

from benchmarks.external.compare import VOLTAGE_PU, compare_buses
from power_grid_env.importer import import_network


def test_voltage_gate_stays_strict():
    native = {
        "converged": True,
        "buses": [{"name": "b", "phases": [{"node": 1, "pu": 1.0, "angle_deg": 0.0}]}],
    }
    close = compare_buses(native, [{"bus": "b", "node": 1, "pu": 1.0 + VOLTAGE_PU / 10, "angle_deg": 0.0}])
    far = compare_buses(native, [{"bus": "b", "node": 1, "pu": 1.0 + VOLTAGE_PU * 10, "angle_deg": 0.0}])
    assert close["verdict"] == "pass"
    assert far["verdict"] == "fail"
    empty = compare_buses({"converged": True, "buses": []}, [{"bus": "b", "node": 1, "pu": 1.0, "angle_deg": 0.0}])
    assert empty["verdict"] == "fail"
    missing = compare_buses(
        {"converged": True, "buses": []},
        [],
    )
    assert missing["verdict"] == "fail"
    nan = compare_buses(
        native,
        [{"bus": "b", "node": 1, "pu": float("nan"), "angle_deg": 0.0}],
    )
    assert nan["verdict"] == "fail"
    assert nan["max_voltage_error_pu"] is None
    deenergized = compare_buses(
        {"converged": True, "buses": [{"name": "b", "phases": [{"node": 1, "pu": 0.0, "angle_deg": 0.0}]}]},
        [{"bus": "b", "node": 1, "pu": 1.0, "angle_deg": 40.0}],
    )
    assert deenergized["verdict"] == "fail"
    assert deenergized["counts"]["angle_skipped"] == 1
    both_dead = compare_buses(
        {"converged": True, "buses": [{"name": "b", "phases": [{"node": 1, "pu": 0.0, "angle_deg": 90.0}]}]},
        [{"bus": "b", "node": 1, "pu": 0.0, "angle_deg": 0.0}],
    )
    assert both_dead["verdict"] == "pass"
    duplicated = compare_buses(
        {
            "converged": True,
            "buses": [
                {"name": "b", "phases": [{"node": 1, "pu": 1.0, "angle_deg": 0.0}, {"node": 1, "pu": 1.0, "angle_deg": 0.0}]}
            ],
        },
        [{"bus": "b", "node": 1, "pu": 1.0, "angle_deg": 0.0}],
    )
    assert duplicated["verdict"] == "fail"


def test_sequence_linecode_keeps_zero_sequence(tmp_path: Path):
    (tmp_path / "net.dss").write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=10 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.3",
                "New LineCode.cab nphases=3 r1=0.3 x1=0.4 r0=0.9 x0=1.2 units=km",
                "New Line.l bus1=a bus2=b phases=3 length=1 units=km linecode=cab",
                "New Load.b bus1=b phases=3 conn=wye kv=10 kw=10 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "side.yaml").write_text(
        "\n".join(
            [
                "schema_version: power-grid-env.import/v1",
                "network_id: seq",
                "name: seq",
                "s_base_va: 1000000",
                "frequency_hz: 50",
                "impedance_interpretation: sequence_to_phase",
                "grounding:",
                "  Load.b: solid",
                "lines:",
                "  Line.l:",
                "    rating_a: 100",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(tmp_path / "net.dss", tmp_path / "side.yaml")
    assert network is not None, report["errors"]
    # Zс=(2Z1+Z0)/3, Z1=0.3+j0.4, Z0=0.9+j1.2 → self = 0.5+j0.666...
    assert network.lines[0].r_ohm[0][0] == pytest.approx(0.5)
    assert network.lines[0].r_ohm[0][1] == pytest.approx(0.2)
    assert network.lines[0].x_ohm[0][1] == pytest.approx((1.2 - 0.4) / 3)


def test_matrix_linecode_without_cmatrix_keeps_opendss_default_shunt(tmp_path: Path):
    import math

    (tmp_path / "net.dss").write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=12.47 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.1",
                "New LineCode.cab nphases=3 rmatrix=(1 | 0 1 | 0 0 1) xmatrix=(1 | 0 1 | 0 0 1) units=mi",
                "New Line.l bus1=a bus2=b phases=3 length=1 units=mi linecode=cab",
                "New Load.b bus1=b phases=3 conn=wye kv=12.47 kw=10 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(tmp_path / "net.dss")
    assert network is not None, report["errors"]
    omega = 2.0 * math.pi * network.frequency_hz
    assert network.lines[0].b_siemens[0][0] == pytest.approx(2.8e-9 * omega)
    assert network.lines[0].b_siemens[0][1] == pytest.approx(-0.6e-9 * omega)


def test_three_phase_switch_keeps_opendss_small_impedance(tmp_path: Path):
    (tmp_path / "net.dss").write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=12.47 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.1",
                "New LineCode.sw nphases=3 rmatrix=(9 | 0 9 | 0 0 9) xmatrix=(9 | 0 9 | 0 0 9) units=km",
                "New Line.s bus1=a.1.2.3 bus2=b.1.2.3 phases=3 switch=yes length=1 units=km linecode=sw",
                "New Load.b bus1=b phases=3 conn=wye kv=12.47 kw=10 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(tmp_path / "net.dss")
    assert network is not None, report["errors"]
    assert network.lines[0].r_ohm[0][0] == pytest.approx(0.001)
    assert network.lines[0].x_ohm[0][0] == pytest.approx(0.001)
    assert network.lines[0].r_ohm[0][1] == pytest.approx(0.0)


def test_switch_without_length_uses_opendss_short_length(tmp_path: Path):
    (tmp_path / "net.dss").write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=4.16 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.1",
                "New Line.s bus1=a bus2=b phases=3 switch=yes r1=1e-4 x1=0 r0=1e-4 x0=0 c1=0 c0=0",
                "New Load.b bus1=b phases=3 conn=wye kv=4.16 kw=100 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(tmp_path / "net.dss")
    assert network is not None, report["errors"]
    # OpenDSS: length становится 0.001, полное r = 1e-4 * 0.001.
    assert network.lines[0].r_ohm[0][0] == pytest.approx(1e-7)
    assert network.lines[0].x_ohm[0][0] == pytest.approx(0.0)


def test_smoke_pack_records_ieee_and_enwl_without_a_false_pass():
    cache = Path("data/external/raw/ieee-pes/13Bus/IEEE13Nodeckt.dss")
    if not cache.exists():
        pytest.skip("локальный кэш IEEE13 отсутствует")
    from benchmarks.external.run import run_pack

    out = Path("reports/external/smoke")
    payload = run_pack(Path("benchmarks/external/packs/smoke.yaml"), out, offline=True)
    by_id = {item["id"]: item for item in payload["cases"]}
    assert by_id["ieee13"]["native_converged"] is True
    assert by_id["ieee13"]["import_status"] == "converged"
    assert by_id["ieee13"]["verdict"] == "pass"
    assert by_id["ieee13"]["max_voltage_error_pu"] < 1e-5
    assert by_id["enwl-network23-feeder3"]["import_status"] == "converged"
    assert by_id["enwl-network23-feeder3"]["verdict"] == "pass"
    assert by_id["enwl-network23-feeder3"]["max_voltage_error_pu"] < 1e-5
    assert (out / "report.html").exists()


def test_distribution_pack_passes_the_same_voltage_gate():
    cache = Path("data/external/raw/ieee-pes/34Bus/ieee34Mod1.dss")
    if not cache.exists():
        pytest.skip("локальный кэш IEEE34 отсутствует")
    from benchmarks.external.run import run_pack

    out = Path("reports/external/distribution")
    payload = run_pack(Path("benchmarks/external/packs/distribution.yaml"), out, offline=True)
    by_id = {item["id"]: item for item in payload["cases"]}
    for case_id in ("ieee34", "ieee123", "enwl-median", "enwl-largest"):
        assert by_id[case_id]["verdict"] == "pass", case_id
        assert by_id[case_id]["import_status"] == "converged", case_id
        assert by_id[case_id]["max_voltage_error_pu"] < 1e-5
    assert by_id["smart-ds-one-feeder"]["verdict"] == "not_run"


def test_pglib_small_cases_are_locally_feasible_and_not_global():
    from benchmarks.external.pglib_opf import evaluate

    root = Path("data/external/raw/pglib-opf")
    if not (root / "pglib_opf_case3_lmbd.m").exists():
        pytest.skip("локальный кэш PGLib отсутствует")
    case3 = evaluate(root / "pglib_opf_case3_lmbd.m")
    assert case3["verdict"] == "pass"
    assert case3["global_optimum_claimed"] is False
    assert case3["flows_within_rating"] is True
    assert case3["max_mismatch_pu"] < 1e-6
    assert abs(case3["relative_gap_to_published"]) < 1e-4
    for name in ("pglib_opf_case5_pjm.m", "pglib_opf_case14_ieee.m"):
        item = evaluate(root / name)
        assert item["verdict"] == "pass"
        assert item["global_optimum_claimed"] is False
        assert item["published_cost"] is None
    heavy = root / "pglib_opf_case118_ieee.m"
    if heavy.exists():
        item = evaluate(heavy)
        assert item["verdict"] == "pass"
        assert item["global_optimum_claimed"] is False
        assert item["max_mismatch_pu"] < 1e-6
        assert item["flows_within_rating"] is True
        # Таблица PGLib округляет AC-цель до 9.7214e4. Это сверка числа, не доказательство глобальности.
        assert item["cost"] == pytest.approx(97214, abs=1)


def test_simbench_snapshots_match_pandapower_voltage_magnitude():
    cache = Path("data/external/raw/simbench/1-complete_data-mixed-all-0-sw/Node.csv")
    if not cache.exists():
        pytest.skip("локальный кэш SimBench отсутствует")
    pytest.importorskip("pandapower")
    from benchmarks.external.simbench_network import CASES, CROSS_SOLVER_VOLTAGE_PU, compare_pandapower

    assert CROSS_SOLVER_VOLTAGE_PU == 1e-4
    for code in CASES:
        compared = compare_pandapower(code)
        assert compared["verdict"] == "pass", code
        assert compared["angles_compared"] is False
        assert compared["same_engine_gate_claimed"] is False
        assert compared["max_voltage_pu"] <= 1e-4


def test_enwl_profile_library_is_not_joined_to_a_feeder():
    if not Path("data/external/raw/enwl-lvns/lct-profiles.zip").exists():
        pytest.skip("архив профилей ENWL отсутствует")
    from benchmarks.external.enwl_profiles import inventory

    payload = inventory()
    assert payload["feeder_day"] == "data_unavailable"
    assert payload["join_key_found"] is False
    assert payload["shape_files_referenced"][0].startswith("C:\\")
    assert len(payload["books"]) == 6
    assert {item["columns"] for item in payload["books"]} == {100}
    assert {item["header"] for item in payload["books"]} == {False}


def test_derived_operating_points_keep_the_same_gate(tmp_path: Path):
    cache = Path("data/external/raw/ieee-pes/13Bus/IEEE13Nodeckt.dss")
    if not cache.exists():
        pytest.skip("локальный кэш IEEE13 отсутствует")
    from benchmarks.external.compare import VOLTAGE_PU
    from benchmarks.external.derived_scenarios import run_pack

    assert VOLTAGE_PU == 1e-5
    payload = run_pack(tmp_path)
    by_id = {item["id"]: item for item in payload["cases"]}
    for case_id in (
        "ieee13-frozen",
        "ieee13-load_0_5",
        "ieee34-frozen",
        "ieee34-load_0_5",
        "ieee34-load_1_2",
        "ieee123-frozen",
        "ieee123-load_0_5",
        "ieee123-load_1_2",
        "ieee123-open_sw1",
        "ieee13-load_1_2",
    ):
        assert by_id[case_id]["verdict"] == "pass", case_id
        assert by_id[case_id]["max_voltage_error_pu"] < 1e-5
    assert payload["thresholds"]["voltage_pu"] == 1e-5
    assert payload["verdict"] == "pass"


def test_smart_ds_qsts_replay_matches_the_continuous_run(tmp_path: Path):
    master = Path(
        "data/external/raw/smart-ds/v1.0/2018/SFO/P1U/opendss/"
        "p1uhs0_1247/p1uhs0_1247--p1udt104/Master.dss"
    )
    if not master.exists():
        pytest.skip("локальный фидер SMART-DS отсутствует")
    from benchmarks.external.smart_ds_qsts import _replay, run

    payload = run(tmp_path, steps=4)
    assert payload["verdict"] == "pass"
    assert payload["max_voltage_error_pu"] < 1e-5
    assert payload["replay"]["verdict"] == "pass"
    assert payload["replay"]["cursor"] == 2
    assert payload["replay"]["max_voltage_error_pu"] < 1e-5
    assert (tmp_path / "checkpoint_4.json").exists()
    rejected = _replay(
        {"profile_sha256": {"other": "0"}, "cursor": 1, "sha256": "x"},
        {},
        {},
        [],
        4,
        payload["profile_sha256"],
    )
    assert rejected["verdict"] == "fail"


def test_field_pack_does_not_score_missing_measurements(tmp_path: Path):
    from benchmarks.external.run import run_pack

    request = Path("benchmarks/external/partner_request.md").read_text(encoding="utf-8")
    assert "НЕ ОТПРАВЛЕНО" in request
    payload = run_pack(Path("benchmarks/external/packs/field-pilot.yaml"), tmp_path, offline=True)
    assert payload["verdict"] == "not_assessed_missing_paired_measurements"
    assert payload["score_computed"] is False
    assert payload["request_sent"] is False
    assert payload["cases"] == []
    assert payload["thresholds_frozen_not_applied"]["voltage_mae_pu"] == 0.005

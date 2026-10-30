"""Площадка 110/10/0,4 кВ: место КЗ, нагрузка и уставка меняют вывод."""

import json

from power_grid_env.cli import main
from power_grid_env.plant_study import run_plant_study


def test_plant_study_changes_the_conclusion_and_keeps_the_profile_closed(tmp_path):
    report = run_plant_study()
    assert report["verdict"] == "pass", report
    assert report["scenario_count"] >= 20
    assert len(report["expected_violations"]) >= 5
    assert report["profile_released"] is False
    assert report["dynamic_motor_start"] is False
    assert report["field_status"] == "waiting_for_data"
    assert report["losses_w"] > 100.0
    by_id = {row["id"]: row for row in report["scenarios"]}
    assert by_id["fault_mv_a"]["fed_from"] == "a"
    assert by_id["fault_mv_b"]["fed_from"] == "b"
    assert by_id["fault_mv_a"]["current_a"] > 10.0 * by_id["fault_mv_a"]["current_b"]
    assert by_id["light_load"]["voltage_pu"] >= 0.95
    assert by_id["heavy_load"]["voltage_pu"] < 0.95
    assert by_id["light_load"]["voltage_pu"] > by_id["heavy_load"]["voltage_pu"]
    assert by_id["sensitive_pickup"]["operate_s"] is not None
    assert by_id["blind_pickup"]["operate_s"] is None
    assert by_id["both_out"]["violation"] is True
    assert by_id["parallel_forbidden"]["violation"] is True
    assert by_id["motor_rejected"]["status"] == "unsupported_motor"
    assert by_id["harmonic_rejected"]["violation"] is True
    assert by_id["section_interlock"]["status"] == "parallel_infeed"
    assert by_id["qsts_minute"]["violation"] is False
    assert main(["plant-study", "--out", str(tmp_path)]) == 0
    saved = json.loads((tmp_path / "plant-study.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "pass"
    assert saved["profile_released"] is False

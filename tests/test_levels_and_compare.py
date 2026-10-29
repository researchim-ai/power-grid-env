"""Связка уровней одного включения и двухшинное сравнение с ANDES."""

from power_grid_env.compare_pf import qualify_two_bus, qualify_wheel
from power_grid_env.coupling import qualify_event_levels


def test_one_rl_event_agrees_from_the_phasor_to_the_waveform():
    report = qualify_event_levels()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert 3.0 < report["step_ratio"] < 5.0
    assert report["boundary_ratio"] < 0.5
    assert abs(report["energy_imbalance_j"]) < 1e-6
    assert report["field_status"] == "waiting_for_data"
    assert report["full_emt"] is False
    assert report["synthetic_is_field"] is False


def test_two_bus_voltage_matches_andes_without_a_superiority_claim():
    report = qualify_two_bus()
    assert report["verdict"] == "pass", report
    assert report["voltage_error_pu"] < 1e-5
    assert report["angle_error_rad"] < 1e-4
    assert report["competitive_superiority_proven"] is False
    assert report["city_claim"] is False
    assert report["other_machine_install"] is False


def test_wheel_imports_from_an_empty_directory():
    report = qualify_wheel()
    assert report["verdict"] == "pass", report
    assert report["version"] == "0.1.0"
    assert report["other_machine"] is False
    assert report["released"] is False

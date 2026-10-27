"""Независимый MNA против фазного расчёта. Пороги плана не ослабляются."""

from power_grid_env.phase_oracle import ANGLE_RAD, KCL_PU, VOLTAGE_PU, qualify


def test_unbalanced_four_wire_matches_the_independent_mna():
    report = qualify()
    assert report["verdict"] == "pass", report
    assert report["evidence"] == "independent_mna"
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["max_angle_error_rad"] < ANGLE_RAD
    assert report["max_sequence_error_pu"] < VOLTAGE_PU
    assert report["max_neutral_or_line_voltage_error_pu"] < VOLTAGE_PU
    assert report["kcl_pu"] < KCL_PU
    assert report["neutral_voltage_pu"] > 0.0
    assert report["loss_error_w"] < 1e-3

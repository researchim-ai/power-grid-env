"""Дистанционный луч по фазному Z и пересчёт реактивности с частотой."""

from power_grid_env.distance import qualify, qualify_frequency_scaling


def test_distance_zone_sees_the_line_and_does_not_claim_a_transient_filter():
    report = qualify()
    assert report["verdict"] == "pass", report
    assert report["dynamic_qualified"] is False
    assert report["impedance_error"] < 1e-4


def test_halving_frequency_scales_reactance_in_the_phase_solver():
    report = qualify_frequency_scaling()
    assert report["verdict"] == "pass", report
    assert report["not_a_pwm_drive"] is True
    assert report["half_a"] > report["full_a"]

"""Классическая машина ANDES против аналитической модели качаний."""

from power_grid_env.andes_rms import qualify_classical_machine
from power_grid_env.dynamics import release_status


def test_andes_classical_machine_matches_the_analytic_swing():
    report = qualify_classical_machine()
    assert report["verdict"] == "pass", report
    assert report["andes_version"].startswith("2.")
    assert report["angle_error_rad"] < 1e-6
    assert report["init_residual"] < 1e-8
    assert report["undisturbed_angle_rad"] < 1e-5
    assert report["trajectory_error_rad"] < 1e-4
    assert report["trajectory_error_rad"] < report["coarse_error_rad"]
    assert report["industrial_rms_qualified"] is False
    assert release_status()["andes_status"] == "installed"
    assert release_status()["andes_integrated"] is False
    assert release_status()["industrial_rms_qualified"] is False

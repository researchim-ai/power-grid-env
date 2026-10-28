"""Десять проверок плана. Пороги заданы в industrial_batch и здесь не ослабляются."""

from power_grid_env.industrial_batch import (
    qualify_capacitor,
    qualify_directional,
    qualify_energy,
    qualify_ll,
    qualify_llg,
    qualify_ltc,
    qualify_resonance,
    qualify_selectivity,
    qualify_slg,
    qualify_thermal,
)


def test_ltc_steps_once_holds_inside_the_band_and_stops_at_the_limit():
    report = qualify_ltc()
    assert report["verdict"] == "pass", report
    assert report["cases"] == {"raise": 1.01, "hold": 1.0, "limit": 1.1}


def test_capacitor_hysteresis_and_both_steps_match_the_nodal_solution():
    report = qualify_capacitor()
    assert report["verdict"] == "pass", report
    assert report["voltage_pu"][1] > report["voltage_pu"][0]


def test_thermal_integrator_reaches_the_formula_and_does_not_change_power_flow():
    report = qualify_thermal()
    assert report["verdict"] == "pass", report
    assert report["power_flow_delta_pu"] == 0.0
    assert report["steady_error_c"] < 1e-3


def test_two_profile_intervals_conserve_energy_and_are_not_a_week():
    report = qualify_energy()
    assert report["verdict"] == "pass", report
    assert report["horizon_s"] == 60
    assert report["not_a_week"] is True


def test_single_line_to_ground_matches_the_sequence_current():
    report = qualify_slg()
    assert report["verdict"] == "pass", report
    assert report["not_iec_60909"] is True
    assert report["max_current_error"] < 1e-5


def test_line_to_line_matches_the_sequence_current():
    report = qualify_ll()
    assert report["verdict"] == "pass", report
    assert report["max_current_error"] < 1e-5


def test_double_line_to_ground_matches_the_phase_impedance_matrix():
    report = qualify_llg()
    assert report["verdict"] == "pass", report
    assert report["not_iec_60909"] is True
    assert report["max_current_error"] < 1e-5


def test_downstream_relay_trips_before_upstream_and_backup_covers_a_failure():
    report = qualify_selectivity()
    assert report["verdict"] == "pass", report
    assert report["near_s"] < report["far_s"]
    assert report["backup_s"] == report["far_s"]


def test_reverse_power_does_not_trip_a_forward_relay():
    report = qualify_directional()
    assert report["verdict"] == "pass", report
    assert report["forward_trip_s"] == 0.1


def test_three_winding_star_matches_the_phase_solution():
    from power_grid_env.industrial_batch import qualify_three_winding

    report = qualify_three_winding()
    assert report["verdict"] == "pass", report
    assert report["max_voltage_error_pu"] < 1e-5
    assert report["max_angle_error_rad"] < 1e-4
    assert report["loss_error_w"] < 1e-3


def test_capacitor_steps_after_its_delay_and_survives_a_snapshot():
    from power_grid_env.industrial_batch import qualify_capacitor_engine

    report = qualify_capacitor_engine()
    assert report["verdict"] == "pass", report
    assert report["steps_in"] == 1
    assert report["measured_pu"] < 1.0


def test_degenerate_capacitor_control_is_rejected():
    import pytest

    from power_grid_env.build import NetBuilder
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    builder = NetBuilder("bad-cap", "Батарея", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.2 + 0.4j)
    builder.load("ld", 10_000.0, 0.0, load_id="load")
    builder.shunt("ld", 50.0, shunt_id="cap")
    network = builder.build()
    network.shunts[0].steps_in = 0
    engine = Engine(
        network,
        Scenario(
            id="bad-cap",
            horizon_s=30,
            settle_s=600,
            control_interval_s=30,
            capacitor_controls=[
                {
                    "shunt_id": "cap",
                    "measured_node_id": "ld",
                    "close_below_pu": 1.0,
                    "open_above_pu": 1.0,
                    "delay_s": 5.0,
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="гистерезис"):
        engine.reset()


def test_series_rlc_resonance_and_triplen_neutral_are_analytic():
    report = qualify_resonance()
    assert report["verdict"] == "pass", report
    assert report["not_a_measured_spectrum"] is True
    assert abs(report["residual_imag"]) < 1e-9

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


def test_mesh_of_two_sources_matches_the_independent_mna_and_one_source_absorbs():
    from power_grid_env.phase_oracle import qualify_mesh

    report = qualify_mesh()
    assert report["verdict"] == "pass", report
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["max_angle_error_rad"] < ANGLE_RAD
    assert report["kcl_pu"] < KCL_PU
    assert report["max_current_error_a"] < 1e-4
    assert report["loss_error_w"] < 1e-3
    assert report["source_power_w"]["right"] < 0.0
    assert report["source_power_w"]["left"] > 0.0


def test_zip_matches_its_own_newton_and_differs_from_constant_power():
    from power_grid_env.phase_oracle import qualify_zip

    report = qualify_zip()
    assert report["verdict"] == "pass", report
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["kcl_pu"] < KCL_PU
    assert abs(report["voltage_pu"] - report["pq_voltage_pu"]) > 1e-4


def test_pv_reactive_limit_matches_the_independent_newton():
    from power_grid_env.phase_oracle import qualify_pv_limit

    report = qualify_pv_limit()
    assert report["verdict"] == "pass", report
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["max_angle_error_rad"] < ANGLE_RAD
    assert report["kcl_pu"] < KCL_PU
    assert report["q_required_var"] > 5_000.0
    assert report["voltage_pu"] < 0.999


def test_floating_neutral_wye_matches_the_independent_mna():
    from power_grid_env.phase_oracle import qualify_floating

    report = qualify_floating()
    assert report["verdict"] == "pass", report
    assert report["neutral_voltage_pu"] > 0.01
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["max_angle_error_rad"] < ANGLE_RAD
    assert report["kcl_pu"] < KCL_PU
    assert report["loss_error_w"] < 1e-3


def test_dyn11_with_tap_matches_the_positive_sequence_model():
    from power_grid_env.phase_oracle import qualify_dyn11

    report = qualify_dyn11()
    assert report["verdict"] == "pass", report
    assert report["max_voltage_error_pu"] < VOLTAGE_PU
    assert report["max_angle_error_rad"] < ANGLE_RAD
    assert report["kcl_pu"] < KCL_PU
    assert report["loss_error_w"] < 1e-3
    assert abs(report["shift_deg"] - 30.0) < 2.0


def test_ungrounded_wye_without_a_neutral_node_is_rejected():
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("no-n", "Без нейтрали", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.2 + 0.4j)
    builder.load("ld", 10_000.0, 0.0, model="constant_z", ground="ungrounded", load_id="load")
    solved = solve_network(builder.build())
    assert solved.status == "invalid_model"
    assert any("нейтрал" in item for item in solved.warnings)



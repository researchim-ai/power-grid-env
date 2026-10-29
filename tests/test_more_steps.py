"""Здоровые фазы, несимметрия, статизм, шунт, угол и разгрузка по напряжению."""

import math

import pytest

from power_grid_env.more_steps import (
    droop_share,
    qualify_angle,
    qualify_healthy_phases,
    qualify_regulation,
    qualify_share,
    qualify_shunt,
    qualify_unbalance,
)
from power_grid_env.protection import breaker_failure_time, undervoltage_shed


def test_healthy_phase_voltage_rises_when_zero_sequence_is_large():
    report = qualify_healthy_phases()
    assert report["verdict"] == "pass", report
    assert report["voltage_error"] < 1e-4
    assert report["healthy_pu"] > 1.2


def test_a_lagging_load_drops_the_voltage_more_than_a_leading_one():
    report = qualify_regulation()
    assert report["verdict"] == "pass", report
    assert report["divider_error"] < 1e-5


def test_one_phase_is_fully_unbalanced_and_sequence_power_matches():
    report = qualify_unbalance()
    assert report["verdict"] == "pass", report
    assert report["single_ratio"] == pytest.approx(1.0, abs=1e-3)
    assert report["balanced_ratio"] < 1e-3


def test_droop_share_is_inverse_and_the_source_is_left_with_the_residual():
    report = qualify_share()
    assert report["verdict"] == "pass", report
    assert report["share_w"] == pytest.approx([100_000.0, 200_000.0])
    assert report["source_error"] < 1e-3
    assert droop_share(90_000.0, [1.0, 1.0, 1.0]) == pytest.approx([30_000.0, 30_000.0, 30_000.0])


def test_a_shunt_supplies_its_voltage_squared_vars_and_raises_the_bus():
    report = qualify_shunt()
    assert report["verdict"] == "pass", report
    assert report["reactive_error"] < 1e-3


def test_the_receiving_angle_matches_the_divider():
    report = qualify_angle()
    assert report["verdict"] == "pass", report
    assert report["angle_error"] < 1e-4


def test_breaker_failure_starts_the_backup_timer():
    assert breaker_failure_time(0.2, 0.15, 0.5, failed=False) == 0.2
    assert breaker_failure_time(0.2, 0.15, 0.5, failed=True) == pytest.approx(0.35)
    assert breaker_failure_time(0.2, 0.25, 0.3, failed=True) == pytest.approx(0.3)
    assert math.isinf(breaker_failure_time(math.inf, 0.15, 0.5, failed=True))
    with pytest.raises(ValueError):
        breaker_failure_time(0.2, 0.0, 0.5, failed=True)


def test_inverse_curves_cross_once_as_the_current_multiple_grows():
    from power_grid_env.protection import iec_operate_time

    def gap(multiple: float) -> float:
        return iec_operate_time(multiple, 1.0, "standard_inverse", 1.0) - iec_operate_time(multiple, 1.0, "very_inverse", 1.0)

    assert gap(1.5) * gap(20.0) < 0.0
    low, high = 1.5, 20.0
    for _ in range(40):
        mid = 0.5 * (low + high)
        if gap(low) * gap(mid) <= 0.0:
            high = mid
        else:
            low = mid
    assert abs(gap(0.5 * (low + high))) < 1e-4


def test_reclose_waits_for_the_dead_time():
    from power_grid_env.protection import reclose_ready

    assert reclose_ready(0.49, 0.5) is False
    assert reclose_ready(0.5, 0.5) is True
    with pytest.raises(ValueError):
        reclose_ready(0.1, 0.0)


def test_undervoltage_shed_keeps_the_critical_load():
    stages = [(0.90, ["aux", "critical"]), (0.85, ["heat"])]
    critical = {"critical"}
    assert undervoltage_shed(0.95, stages, critical) == []
    assert undervoltage_shed(0.87, stages, critical) == ["aux"]
    assert undervoltage_shed(0.80, stages, critical) == ["aux", "heat"]
    assert undervoltage_shed(0.5, stages, critical, available=False) == []
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("uvls", "Разгрузка", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", 100_000.0, 0.0, load_id="critical")
    solved = solve_network(builder.build())
    assert solved.status == "converged"
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    assert measured == pytest.approx(100_000.0, rel=1e-4)

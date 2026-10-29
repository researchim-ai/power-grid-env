"""Сквозной ток трансформатора не отключает, КЗ внутри зоны отключает."""

import pytest

from power_grid_env.differential import qualify_through_fault
from power_grid_env.protection import transformer_differential


def test_through_current_holds_and_an_internal_fault_trips():
    report = qualify_through_fault()
    assert report["verdict"] == "pass", report
    assert report["dynamic_qualified"] is False
    assert report["operate_fraction"] < 0.02
    assert report["internal_operate_a"] > report["pickup_a"]


def test_a_steep_slope_blocks_a_moderate_mismatch_and_a_missing_ct_does_not_trip():
    through = transformer_differential(10 + 0j, -250 + 0j, 25.0, slope=0.3, pickup_a=20.0)
    assert through["trip"] is False
    mismatch = transformer_differential(10 + 0j, -200 + 0j, 25.0, slope=2.0, pickup_a=20.0)
    assert mismatch["trip"] is False
    assert mismatch["operate_a"] == pytest.approx(50.0)
    blind = transformer_differential(10 + 0j, 0j, 25.0, slope=0.3, pickup_a=20.0, available=False)
    assert blind["trip"] is False
    assert blind["dynamic_qualified"] is False

"""АПВ с блокировкой, плавкая вставка по i²t и статизм после просадки напряжения."""

import math

import numpy as np
import pytest

from power_grid_env.build import NetBuilder
from power_grid_env.dynamics import simulate_smib_with_controls
from power_grid_env.fault_envelope import closed_form, fuse_i2t, fuse_melts
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine


def test_recloser_closes_once_and_locks_out_on_the_second_fault():
    builder = NetBuilder("reclose", "АПВ", 10.0)
    builder.node("src")
    builder.node("mid")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.switch("src", "mid", switch_id="sw", kind="recloser")
    builder.line_z1("mid", "ld", 0.2 + 0.4j, line_id="line")
    builder.load("ld", 200_000.0, 0.0, model="constant_z", load_id="load")
    engine = Engine(
        builder.build(),
        Scenario(
            id="reclose",
            horizon_s=5.0,
            control_interval_s=5.0,
            settle_s=100.0,
            protection=[
                {
                    "id": "rec",
                    "kind": "overcurrent",
                    "equipment_id": "sw",
                    "measured_equipment_id": "line",
                    "pickup": 1.0,
                    "curve": "definite",
                    "time_multiplier_s": 0.2,
                    "max_shots": 2,
                    "reclose_delay_s": 0.5,
                    "reset_s": 0.05,
                }
            ],
        ),
    )
    engine.reset()
    engine.advance_to(0.15)
    assert engine.net.switches[0].closed is True
    engine.advance_to(0.25)
    assert engine.net.switches[0].closed is False
    engine.advance_to(0.7)
    assert engine.net.switches[0].closed is True
    engine.advance_to(1.0)
    assert engine.net.switches[0].closed is False
    assert engine.relays[0].lockout is True
    engine.advance_to(2.0)
    assert engine.net.switches[0].closed is False
    assert engine.relays[0].shots == 2


def test_fuse_melts_only_after_the_i2t_of_the_fault_envelope():
    samples = np.linspace(0.0, 0.1, 20_001)
    squared = np.array([closed_form(float(stamp), 5773.5, 0.35, 0.8, 50.0) ** 2 for stamp in samples])
    integral = float(np.trapezoid(squared, samples))
    let_through = fuse_i2t(0.1, 5773.5, 0.35, 0.8, 50.0)
    assert let_through == pytest.approx(integral, rel=1e-6)
    assert fuse_melts(let_through, let_through) is True
    assert fuse_melts(let_through, let_through * 1.01) is False


def test_after_a_voltage_drop_the_exciter_restores_synchronous_speed():
    from power_grid_env.dynamics import controlled_balance

    balance = controlled_balance(0.5, 1.1, 1.0, 0.3, voltage_scale=0.95)
    assert balance["power_balance"] == pytest.approx(0.0, abs=1e-12)
    assert balance["omega_rad_s"] == pytest.approx(2.0 * math.pi * 50.0)
    assert balance["field"] > 1.1
    with pytest.raises(ValueError, match="предел"):
        controlled_balance(0.5, 1.1, 1.0, 0.3, voltage_scale=0.05)
    trace = simulate_smib_with_controls(
        pm=0.5, e_pu=1.1, v_pu=1.0, x_pu=0.3, inertia_s=4.0, voltage_scale=0.95, dt_s=0.01, t_end_s=0.4
    )
    assert max(float(value) for value in trace["omega"]) > 2.0 * math.pi * 50.0

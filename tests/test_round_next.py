"""Качание, гармоника, паз, ШИМ, корпус ошибок, скачок RL и пуск в темноте."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import bad_data_corpus, dc_power_flow, line_flow_pu
from power_grid_env.conductor import slot_resistance_coth, slot_resistance_factor
from power_grid_env.dynamics import ac_component
from power_grid_env.fault_envelope import stepped_current, stepped_trapezoid
from power_grid_env.further import qualify_black_start
from power_grid_env.harmonic import bipolar_pwm_fundamental
from power_grid_env.protection import harmonic_restraint, swing_block


def test_a_slow_impedance_move_does_not_trip_and_a_jump_does():
    fault = swing_block(2 + 2j, 0.4 + 1.2j, 0.02, minimum_ohm_per_s=5.0, in_zone=True)
    swing = swing_block(2 + 2j, 1.9 + 1.9j, 1.0, minimum_ohm_per_s=5.0, in_zone=True)
    outside = swing_block(2 + 2j, 0.4 + 1.2j, 0.02, minimum_ohm_per_s=5.0, in_zone=False)
    blind = swing_block(2 + 2j, 0.4 + 1.2j, 0.02, minimum_ohm_per_s=5.0, in_zone=True, available=False)
    assert fault["trip"] is True and fault["reason"] == "fault"
    assert swing["trip"] is False and swing["reason"] == "swing"
    assert outside["reason"] == "outside"
    assert blind["reason"] == "measurement_unavailable"
    assert fault["dynamic_qualified"] is False


def test_second_harmonic_blocks_the_differential():
    blocked = harmonic_restraint(1000.0, 100.0, 20.0, pickup_a=100.0, second_ratio=0.15)
    clear = harmonic_restraint(1000.0, 100.0, 5.0, pickup_a=100.0, second_ratio=0.15)
    quiet = harmonic_restraint(10.0, 100.0, 1.0, pickup_a=100.0, second_ratio=0.15)
    blind = harmonic_restraint(1000.0, 100.0, 1.0, pickup_a=100.0, second_ratio=0.15, available=False)
    assert blocked["trip"] is False and blocked["reason"] == "second_harmonic"
    assert clear["trip"] is True
    assert quiet["reason"] == "below_pickup"
    assert blind["dynamic_qualified"] is False and blind["trip"] is False


def test_ac_component_starts_at_subtransient_and_ends_at_steady():
    assert ac_component(0.0, 5.0, 3.0, 1.0, 0.05, 1.0) == 5.0
    assert ac_component(1.0e6, 5.0, 3.0, 1.0, 0.05, 1.0) == pytest.approx(1.0, abs=1e-6)
    half = ac_component(0.05, 5.0, 3.0, 1.0, 0.05, 1.0)
    assert half == pytest.approx((5 - 3) * math.exp(-1) + (3 - 1) * math.exp(-0.05) + 1)
    with pytest.raises(ValueError):
        ac_component(0.1, 1.0, 2.0, 0.5, 0.05, 1.0)


def test_rectangular_slot_factors_agree_and_start_at_one():
    assert slot_resistance_factor(0.05) == pytest.approx(1.0, abs=1e-4)
    assert slot_resistance_factor(0.05) == pytest.approx(slot_resistance_coth(0.05), rel=1e-8)
    assert slot_resistance_factor(4.0) == pytest.approx(slot_resistance_coth(4.0), rel=1e-8)
    assert slot_resistance_factor(8.0) == pytest.approx(8.0, rel=0.05)
    with pytest.raises(ValueError):
        slot_resistance_factor(0.0)


def test_bipolar_pwm_fundamental_follows_the_modulation_index():
    dc = 100.0
    for modulation in (0.0, 0.4, 0.8):
        measured = bipolar_pwm_fundamental(modulation, dc, 45, 200)
        assert measured == pytest.approx(modulation * dc / 2.0, abs=0.02 * dc)
    with pytest.raises(ValueError):
        bipolar_pwm_fundamental(1.2, dc, 45, 200)


def test_bad_data_corpus_covers_clean_biased_and_singular_sets():
    rows = {item["id"]: item for item in bad_data_corpus()}
    assert rows["clean"]["removed"] == []
    assert rows["one"]["removed"] == [2]
    assert rows["two"]["removed"] == [2, 3]
    assert rows["two"]["estimate"] == pytest.approx(5.0)
    assert rows["singular"]["observable"] is False
    assert rows["wide_variance"]["removed"] == []


def test_rl_current_is_continuous_when_the_voltage_steps():
    resistance, reactance = 0.4, 1.2
    exact = stepped_current(0.03, 0.01, 1000.0, 400.0, resistance, reactance, 50.0)
    at_switch = stepped_current(0.01, 0.01, 1000.0, 400.0, resistance, reactance, 50.0)
    before = stepped_current(0.01, 0.02, 1000.0, 400.0, resistance, reactance, 50.0)
    assert at_switch == pytest.approx(before)
    coarse = abs(stepped_trapezoid(0.03, 2e-4, 0.01, 1000.0, 400.0, resistance, reactance, 50.0) - exact)
    fine = abs(stepped_trapezoid(0.03, 1e-4, 0.01, 1000.0, 400.0, resistance, reactance, 50.0) - exact)
    assert coarse / fine == pytest.approx(4.0, rel=0.25)


def test_black_start_accepts_only_a_forming_bus_above_the_voltage_floor():
    report = qualify_black_start()
    assert report["verdict"] == "pass", report
    assert report["light_pu"] > 0.9
    assert report["heavy_pu"] < 0.5


def test_the_remaining_generator_takes_the_tripped_machine_share():
    reactance = 0.25
    coupled = np.array([[4.0, -4.0], [-4.0, 4.0]])
    with_both = np.array([0.5, -0.5])
    theta = dc_power_flow(coupled, with_both, slack=0)
    assert line_flow_pu(theta, 0, 1, reactance) == pytest.approx(0.5)
    alone = dc_power_flow(coupled, np.array([0.8, -0.8]), slack=0)
    assert line_flow_pu(alone, 0, 1, reactance) == pytest.approx(0.8)
    assert line_flow_pu(alone, 0, 1, reactance) - line_flow_pu(theta, 0, 1, reactance) == pytest.approx(0.3)


def test_constant_current_load_stays_put_when_the_voltage_changes():
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    def ampere(scale: float) -> float:
        builder = NetBuilder(f"ci{scale}", "Ток", 10.0, s_base_va=1.0e6)
        builder.node("src")
        builder.source("src", 0.02, 0.05, ll_v=10_000.0 * scale)
        builder.load("src", 200_000.0, 0.0, model="constant_i", load_id="load")
        solved = solve_network(builder.build())
        assert solved.status == "converged"
        return abs(next(item.current for item in solved.flows if item.equipment_id == "load" and item.phase == "A"))

    assert ampere(1.0) == pytest.approx(ampere(0.8), rel=1e-3)


def test_pwm_carrier_is_not_the_fundamental_bin():
    wave_bin = bipolar_pwm_fundamental(0.8, 100.0, 21, 400)
    assert wave_bin == pytest.approx(40.0, abs=2.0)
    assert wave_bin > 10.0

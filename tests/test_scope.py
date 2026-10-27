"""Паспорт режима и совпадение direct, process и инкрементального расчёта."""

import pytest

from power_grid_env.build import NetBuilder
from power_grid_env.model import Network
from power_grid_env.preflight import preflight
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine


def _feeder():
    builder = NetBuilder("scope", "scope", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.01, 0.05)
    builder.line_z1("src", "ld", 0.2 + 0.4j, line_id="line")
    builder.load("ld", 100_000.0, 20_000.0, load_id="load")
    builder.generator("ld", 20_000.0, generator_id="gen")
    builder.switch("src", "ld", switch_id="tie", closed=False)
    return builder.build()


def _magnitudes(result):
    return {
        (item.bus_id, item.phase): abs(item.complex) / item.v_base_ln_v
        for item in result.voltages
        if item.v_base_ln_v and item.phase in "ABC"
    }


def test_roundtrip_keeps_impedance_and_provenance():
    network = _feeder()
    restored = Network.model_validate_json(network.model_dump_json())
    assert restored.lines[0].r_ohm == network.lines[0].r_ohm
    assert restored.lines[0].x_ohm == network.lines[0].x_ohm
    assert restored.lines[0].provenance.quality == network.lines[0].provenance.quality
    assert restored.generators[0].inertia_s is None


def test_other_studies_do_not_invent_missing_parameters():
    network = _feeder()
    harmonic = preflight(network, task="harmonic", solve_initial=False)
    assert harmonic["ok"] is False
    assert any("частотн" in item for item in harmonic["errors"])
    assert any("спектр" in item["reason"] for item in harmonic["scope"]["blocked"])
    rms = preflight(network, task="rms", solve_initial=False)
    assert rms["ok"] is False
    assert any("не взяты" in item for item in rms["errors"])
    short = preflight(network, task="short_circuit", solve_initial=False)
    assert short["ok"] is False
    assert any("нулевая последовательность" in item for item in short["errors"])
    power = preflight(network, task="power_flow", solve_initial=False)
    assert power["ok"] is True
    payload = network.switches[0].model_dump()
    payload["representation"] = "series"
    with pytest.raises(ValueError, match="последовательный аппарат"):
        network.switches[0].__class__.model_validate(payload)


def test_direct_process_incremental_and_checkpoint_match():
    from power_grid_env.backend_opendss import solve_loads_incrementally, solve_network
    from power_grid_env.backend_process import ProcessBackend

    network = _feeder()
    direct = solve_network(network)
    backend = ProcessBackend()
    try:
        remote = backend.solve(network)
    finally:
        backend.close()
    left = _magnitudes(direct)
    right = _magnitudes(remote)
    assert left.keys() == right.keys()
    assert max(abs(left[key] - right[key]) for key in left) < 1e-5
    edited = network.model_copy(deep=True)
    for load in edited.loads:
        for phase in load.p_w:
            load.p_w[phase] *= 1.2
    cold = solve_network(edited)
    warm = solve_loads_incrementally(network, edited)
    cold_v = _magnitudes(cold)
    warm_v = _magnitudes(warm)
    assert max(abs(cold_v[key] - warm_v[key]) for key in cold_v) < 1e-5
    scenario = Scenario(id="scope", horizon_s=120, control_interval_s=60, settle_s=10_000)
    engine = Engine(network, scenario)
    engine.reset()
    engine.step_control({"kind": "hold"})
    restored = Engine(network.model_copy(deep=True), scenario)
    restored.restore(engine.snapshot())
    assert restored.time_s == pytest.approx(engine.time_s)
    resumed = _magnitudes(restored.last_result)
    live = _magnitudes(engine.last_result)
    assert max(abs(resumed[key] - live[key]) for key in live) < 1e-5

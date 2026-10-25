"""Один эпизод восстановления на импортированном фидере."""

from pathlib import Path

from power_grid_env.heuristic import choose_action
from power_grid_env.importer import import_network
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model

network, report = import_network(
    Path("cases/acceptance/feeder_north/feeder.dss"),
    Path("cases/acceptance/feeder_north/pge-import.yaml"),
)
if network is None:
    raise SystemExit(report["errors"])
scenario = Scenario(
    id="example",
    seed=1,
    horizon_s=240,
    control_interval_s=60,
    settle_s=60,
    fault_line_id="damaged",
    fault_kind="3ph",
    fault_phases=["A", "B", "C"],
    fault_r_ohm=1.0,
)
engine = Engine(network, scenario)
public = scenario.public_config(agent_model(network, scenario).model_dump(mode="json"))
engine.reset()
while not engine.terminated and not engine.truncated:
    engine.step_control(choose_action(engine.observation(), public["network"], respect_rating=True))
print([(step["time_s"], step["action"], round(step["energy_not_served_wh"], 1)) for step in engine.trace])

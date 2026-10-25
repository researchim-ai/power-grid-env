"""Короткий rollout Gymnasium. Не является обучением и не сравнивается с baseline как победа."""

from pathlib import Path

from power_grid_env.gym_env import RestorationEnv
from power_grid_env.importer import import_network
from power_grid_env.scenarios import Scenario

network, _report = import_network(
    Path("cases/acceptance/feeder_north/feeder.dss"),
    Path("cases/acceptance/feeder_north/pge-import.yaml"),
)
scenario = Scenario(
    id="rl-demo",
    seed=1,
    horizon_s=120,
    control_interval_s=60,
    settle_s=600,
    fault_line_id="damaged",
    fault_kind="3ph",
    fault_phases=["A", "B", "C"],
    fault_r_ohm=1.0,
)
env = RestorationEnv(network, scenario)
observation, info = env.reset(seed=1)
observation, reward, terminated, truncated, info = env.step(0)
print(info["solve_status"], reward, terminated, truncated)

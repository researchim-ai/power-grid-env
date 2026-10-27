"""Gymnasium-среда восстановления. Наблюдение не содержит скрытого состояния."""

from __future__ import annotations

from typing import Any

import numpy as np
from gymnasium import Env, spaces

from power_grid_env.model import Network
from power_grid_env.simulation import Engine


class RestorationEnv(Env):
    metadata = {"render_modes": []}

    def __init__(self, network: Network, scenario: Any) -> None:
        super().__init__()
        self.network = network
        self.scenario = scenario
        self.engine = Engine(network, scenario)
        self.switch_ids = [switch.id for switch in network.switches if switch.operable]
        self.channels = _voltage_channels(network)
        width = len(self.channels)
        self.action_space = spaces.Discrete(int(len(self.switch_ids) * 2 + 1))
        self.observation_space = spaces.Dict(
            {
                "switch_closed": spaces.MultiBinary(max(1, len(self.switch_ids))),
                "voltage_pu": spaces.Box(-1.0, 2.0, shape=(width,), dtype=np.float32),
                "voltage_valid": spaces.MultiBinary(width),
                "measurement_age_s": spaces.Box(0.0, 1.0e9, shape=(width,), dtype=np.float32),
                "measurement_latency_s": spaces.Box(0.0, 1.0e9, shape=(width,), dtype=np.float32),
                "action_mask": spaces.MultiBinary(int(self.action_space.n)),
                "time_s": spaces.Box(0.0, 1.0e9, shape=(1,), dtype=np.float32),
            }
        )

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        observation = self.engine.reset(seed=seed)
        return self._vector(observation), {"solve_status": None if self.engine.last_result is None else self.engine.last_result.status}

    def step(self, action: int):
        mapped = self._decode(int(action))
        record = self.engine.step_control(mapped, origin="controller")
        terminated = bool(self.engine.terminated)
        truncated = bool(self.engine.truncated) and not terminated
        return self._vector(record["observation"]), float(record["reward"]), terminated, truncated, {
            "solve_status": record["solve_status"],
            "violations": record["violations"],
        }

    def _decode(self, action: int) -> dict[str, Any]:
        if action <= 0 or not self.switch_ids:
            return {"kind": "hold"}
        index = (action - 1) // 2
        command = "open" if (action - 1) % 2 == 0 else "close"
        if index >= len(self.switch_ids):
            return {"kind": "hold"}
        return {"kind": "switch", "equipment_id": self.switch_ids[index], "command": command}

    def _vector(self, observation: dict[str, Any]) -> dict[str, np.ndarray]:
        closed = {item["id"]: item["closed"] for item in observation.get("switches", [])}
        flags = np.array([1 if closed.get(switch_id, 0) else 0 for switch_id in self.switch_ids] or [0], dtype=np.int8)
        if flags.shape[0] < self.observation_space["switch_closed"].shape[0]:
            flags = np.pad(flags, (0, self.observation_space["switch_closed"].shape[0] - flags.shape[0]))
        width = len(self.channels)
        volts = np.zeros(width, dtype=np.float32)
        valid = np.zeros(width, dtype=np.int8)
        ages = np.zeros(width, dtype=np.float32)
        latency = np.zeros(width, dtype=np.float32)
        now = float(observation.get("time_s", 0.0))
        by_channel = {
            (str(reading.get("meter_id", "")), str(reading.get("phase", ""))): reading
            for reading in observation.get("voltages", [])
        }
        for index, channel in enumerate(self.channels):
            reading = by_channel.get((channel["meter_id"], channel["phase"]))
            if reading is None or not channel["meter_id"]:
                continue
            volts[index] = float(reading["v_pu"])
            valid[index] = 1
            produced = float(reading.get("produced_s", reading.get("available_s", now)))
            available = float(reading.get("available_s", produced))
            ages[index] = max(0.0, now - produced)
            latency[index] = max(0.0, available - produced)
        mask_lookup = {(item["equipment_id"], item["command"]): item["allowed"] for item in observation.get("action_mask", [])}
        mask = np.zeros(self.action_space.n, dtype=np.int8)
        mask[0] = 1
        for index, switch_id in enumerate(self.switch_ids):
            if mask_lookup.get((switch_id, "open"), False):
                mask[1 + 2 * index] = 1
            if mask_lookup.get((switch_id, "close"), False):
                mask[2 + 2 * index] = 1
        return {
            "switch_closed": flags[: self.observation_space["switch_closed"].shape[0]],
            "voltage_pu": volts,
            "voltage_valid": valid,
            "measurement_age_s": ages,
            "measurement_latency_s": latency,
            "action_mask": mask,
            "time_s": np.array([now], dtype=np.float32),
        }


def _voltage_channels(network: Network) -> list[dict[str, str]]:
    """Все фазные каналы счётчиков, в стабильном порядке. Обрезки до 8 нет."""
    nodes = {node.id: node for node in network.connectivity_nodes}
    channels = []
    for meter in network.meters:
        if not meter.measures_voltage:
            continue
        phases = [phase for phase in nodes[meter.connectivity_node_id].phases if phase != "N"]
        for phase in phases:
            channels.append({"meter_id": meter.id, "phase": phase})
    return channels or [{"meter_id": "", "phase": ""}]

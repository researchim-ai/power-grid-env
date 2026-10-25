"""Детерминированная эвристика восстановления.

Она пользуется только публичной моделью и наблюдением: известный повреждённый
участок, телеметрированные положения и паспортные токи. Скрытый ток КЗ для
маски не читается.
"""

from __future__ import annotations

import math
from typing import Any


def boundary_switches(network: dict[str, Any], line_id: str) -> list[str]:
    terminals = {item["id"]: item for item in network["terminals"]}
    line = next(item for item in network["lines"] if item["id"] == line_id)
    nodes = {
        terminals[line["terminal_from"]]["connectivity_node_id"],
        terminals[line["terminal_to"]]["connectivity_node_id"],
    }
    found = []
    for switch in network["switches"]:
        ends = {
            terminals[switch["terminal_from"]]["connectivity_node_id"],
            terminals[switch["terminal_to"]]["connectivity_node_id"],
        }
        if ends & nodes:
            found.append(switch["id"])
    return found


def estimate_current_a(network: dict[str, Any], unserved_w: float) -> float:
    level = network["voltage_levels"][0]["nominal_ll_v"]
    if level <= 0:
        return 0.0
    return unserved_w / (math.sqrt(3.0) * level)


def choose_action(observation: dict[str, Any], public_network: dict[str, Any], *, respect_rating: bool) -> dict[str, Any]:
    fault = observation.get("known_fault_section")
    switches = {item["id"]: item for item in observation.get("switches", [])}
    mask = {
        (item["equipment_id"], item["command"]): item["allowed"] for item in observation.get("action_mask", [])
    }
    if fault and any(item["id"] == fault for item in public_network.get("lines", [])):
        for switch_id in boundary_switches(public_network, fault):
            state = switches.get(switch_id)
            if state and state["closed"] and mask.get((switch_id, "open"), False):
                return {
                    "kind": "switch",
                    "equipment_id": switch_id,
                    "command": "open",
                    "rationale": "изоляция известного повреждённого участка",
                }
    if fault:
        unserved = _unserved_power(observation, public_network)
        rating = _min_rating(public_network, fault)
        current = estimate_current_a(public_network, unserved)
        if respect_rating and rating is not None and current > 0.9 * rating:
            return {"kind": "hold", "rationale": "замыкание петлевой связи перегрузит путь по паспортному току"}
        normally_open = [item["id"] for item in public_network.get("switches", []) if not item["closed"]]
        blocked = set(boundary_switches(public_network, fault))
        for switch_id in normally_open:
            if switch_id in blocked:
                continue
            state = switches.get(switch_id)
            if state is None or state["closed"] or not state.get("operable", True):
                continue
            if mask.get((switch_id, "close"), False):
                return {
                    "kind": "switch",
                    "equipment_id": switch_id,
                    "command": "close",
                    "rationale": "питание обесточенной нагрузки по нормально разомкнутой связи",
                }
    return {"kind": "hold", "rationale": "нет допустимого переключения"}


def _unserved_power(observation: dict[str, Any], network: dict[str, Any]) -> float:
    voltages: dict[str, list[float]] = {}
    for reading in observation.get("voltages", []):
        voltages.setdefault(reading["node_id"], []).append(float(reading["v_pu"]))
    total = 0.0
    terminals = {item["id"]: item for item in network["terminals"]}
    limit = float(observation.get("limits", {}).get("v_min_pu", 0.95))
    for load in network.get("loads", []):
        node = terminals[load["terminal_id"]]["connectivity_node_id"]
        values = voltages.get(node)
        if not values or min(values) < limit:
            total += sum(load["p_w"].values())
    return total


def _min_rating(network: dict[str, Any], fault_line: str) -> float | None:
    ratings = [float(line["rating_a"]) for line in network.get("lines", []) if line["id"] != fault_line]
    if not ratings:
        return None
    return min(ratings)

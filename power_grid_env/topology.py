"""Сборка расчётных шин из connectivity-node и разбиение на острова.

Идентификатор шины — функция множества соединённых узлов, поэтому при той же
коммутации он не зависит от порядка обхода. Закрытый коммутационный аппарат
объединяет узлы. Линия и трансформатор соединяют острова, но не сливают шины
разных уровней напряжения.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from power_grid_env.model import Network


@dataclass
class BusInfo:
    id: str
    node_ids: tuple[str, ...]
    phases: tuple[str, ...]
    nominal_ll_v: float
    voltage_level_id: str


@dataclass
class IslandInfo:
    id: str
    bus_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    energized: bool
    reason: str


@dataclass
class Topology:
    buses: dict[str, BusInfo]
    node_to_bus: dict[str, str]
    terminal_bus: dict[str, str]
    islands: list[IslandInfo]
    bus_island: dict[str, str]
    errors: list[str] = field(default_factory=list)


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        self.parent[self.find(right)] = self.find(left)


def bus_identifier(node_ids: list[str]) -> str:
    payload = ",".join(sorted(node_ids)).encode("utf-8")
    return "bus_" + hashlib.sha256(payload).hexdigest()[:16]


def island_identifier(bus_ids: list[str]) -> str:
    payload = ",".join(sorted(bus_ids)).encode("utf-8")
    return "island_" + hashlib.sha256(payload).hexdigest()[:16]


def compile_topology(network: Network) -> Topology:
    nodes = {node.id: node for node in network.connectivity_nodes}
    terminals = {terminal.id: terminal for terminal in network.terminals}
    levels = {level.id: level for level in network.voltage_levels}
    uf = _UnionFind(list(nodes))
    for switch in network.switches:
        if not switch.closed:
            continue
        left = terminals[switch.terminal_from].connectivity_node_id
        right = terminals[switch.terminal_to].connectivity_node_id
        uf.union(left, right)

    groups: dict[str, list[str]] = {}
    for node_id in nodes:
        groups.setdefault(uf.find(node_id), []).append(node_id)

    buses: dict[str, BusInfo] = {}
    node_to_bus: dict[str, str] = {}
    errors: list[str] = []
    for members in groups.values():
        identifier = bus_identifier(members)
        phase_set: list[str] = []
        level_ids = set()
        for node_id in members:
            level_ids.add(nodes[node_id].voltage_level_id)
            for phase in nodes[node_id].phases:
                if phase not in phase_set:
                    phase_set.append(phase)
        if len(level_ids) != 1:
            errors.append(
                f"{identifier}: замкнутые аппараты соединили разные уровни напряжения {sorted(level_ids)}"
            )
            nominal = 0.0
            level_id = sorted(level_ids)[0]
        else:
            level_id = next(iter(level_ids))
            nominal = levels[level_id].nominal_ll_v
        buses[identifier] = BusInfo(
            id=identifier,
            node_ids=tuple(sorted(members)),
            phases=tuple(phase_set),
            nominal_ll_v=nominal,
            voltage_level_id=level_id,
        )
        for node_id in members:
            node_to_bus[node_id] = identifier

    terminal_bus = {
        terminal.id: node_to_bus[terminal.connectivity_node_id] for terminal in network.terminals
    }

    adjacency: dict[str, set[str]] = {bus_id: set() for bus_id in buses}
    for line in network.lines:
        left = terminal_bus[line.terminal_from]
        right = terminal_bus[line.terminal_to]
        adjacency[left].add(right)
        adjacency[right].add(left)
    for transformer in network.transformers:
        winding_buses = [terminal_bus[winding.terminal_id] for winding in transformer.windings]
        for left in winding_buses:
            for right in winding_buses:
                if left != right:
                    adjacency[left].add(right)

    sources_on_bus: dict[str, list[str]] = {bus_id: [] for bus_id in buses}
    for source in network.sources:
        sources_on_bus[terminal_bus[source.terminal_id]].append(source.id)
    forming_on_bus: dict[str, list[str]] = {bus_id: [] for bus_id in buses}
    for inverter in network.inverters:
        if inverter.forming:
            forming_on_bus[terminal_bus[inverter.terminal_id]].append(inverter.id)

    seen: set[str] = set()
    islands: list[IslandInfo] = []
    bus_island: dict[str, str] = {}
    for start in sorted(buses):
        if start in seen:
            continue
        stack = [start]
        member_buses: list[str] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            member_buses.append(current)
            stack.extend(sorted(adjacency[current] - seen))
        source_ids = tuple(
            source_id for bus_id in member_buses for source_id in sources_on_bus[bus_id]
        )
        forming_ids = tuple(
            inverter_id for bus_id in member_buses for inverter_id in forming_on_bus[bus_id]
        )
        energized = bool(source_ids) or bool(forming_ids)
        if source_ids:
            reason = "grid_forming_source"
        elif forming_ids:
            reason = "grid_forming_inverter"
        else:
            reason = "no_grid_forming_source"
        identifier = island_identifier(member_buses)
        islands.append(
            IslandInfo(
                id=identifier,
                bus_ids=tuple(sorted(member_buses)),
                source_ids=source_ids,
                energized=energized,
                reason=reason,
            )
        )
        for bus_id in member_buses:
            bus_island[bus_id] = identifier

    return Topology(
        buses=buses,
        node_to_bus=node_to_bus,
        terminal_bus=terminal_bus,
        islands=islands,
        bus_island=bus_island,
        errors=errors,
    )

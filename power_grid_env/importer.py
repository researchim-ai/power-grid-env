"""Импорт канонического JSON и подмножества OpenDSS.

Внешний формат первой поставки — скрипт DSS плюс обязательный sidecar.
Свойства, которых нет в sidecar (заземление звезды, вид аппарата, перевод
r1/x1 в фазную матрицу), не заполняются молча.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

import yaml

from power_grid_env.model import (
    ConnectivityNode,
    GroundingSpec,
    Line,
    Load,
    Meter,
    Network,
    Provenance,
    Switch,
    Terminal,
    VoltageLevel,
    VoltageSource,
)

_TOKEN = re.compile(r"([A-Za-z0-9_.]+)=(.*)")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def import_network(source: Path, sidecar: Path | None = None) -> tuple[Network | None, dict[str, Any]]:
    source = Path(source)
    report: dict[str, Any] = {
        "source": str(source),
        "source_sha256": file_sha256(source) if source.exists() else None,
        "format": source.suffix.lower().lstrip("."),
        "errors": [],
        "warnings": [],
        "mappings": [],
        "approximations": [],
        "assumptions": [],
    }
    if not source.exists():
        report["errors"].append("файл источника не найден")
        return None, report
    if source.suffix.lower() == ".json":
        try:
            network = Network.model_validate_json(source.read_text(encoding="utf-8"))
        except Exception as exc:
            report["errors"].append(str(exc))
            return None, report
        report["mappings"].append({"from": str(source), "to": "canonical-json"})
        return network, report
    if source.suffix.lower() != ".dss":
        report["errors"].append("поддержаны только .dss + sidecar и канонический .json")
        return None, report
    if sidecar is None:
        report["errors"].append("для DSS обязателен sidecar pge-import.yaml: заземление не выводится из conn=wye")
        return None, report
    side_path = Path(sidecar)
    if not side_path.exists():
        report["errors"].append("sidecar не найден")
        return None, report
    report["sidecar_sha256"] = file_sha256(side_path)
    side = yaml.safe_load(side_path.read_text(encoding="utf-8")) or {}
    try:
        network = _from_dss(source.read_text(encoding="utf-8"), side, report)
    except Exception as exc:
        report["errors"].append(str(exc))
        return None, report
    return network, report


def _from_dss(text: str, side: dict[str, Any], report: dict[str, Any]) -> Network:
    commands = _parse_commands(text)
    circuit = next((item for item in commands if item["kind"] == "circuit"), None)
    if circuit is None:
        raise ValueError("в DSS нет New Circuit")
    base_kv = float(circuit["args"].get("basekv", "0"))
    if base_kv <= 0:
        raise ValueError("Circuit.basekv не задан")
    frequency = float(side.get("frequency_hz", 50.0))
    s_base = float(side.get("s_base_va", 0))
    if s_base <= 0:
        raise ValueError("sidecar.s_base_va обязателен")
    interpretation = side.get("impedance_interpretation")
    if interpretation != "diagonal_from_positive_sequence":
        raise ValueError(
            "r1/x1 не определяют фазную матрицу. В sidecar нужно явно указать "
            "impedance_interpretation: diagonal_from_positive_sequence"
        )
    report["approximations"].append(
        "Последовательные r1/x1 переведены в диагональную фазную матрицу без взаимоиндукции. "
        "Z0 не измерялся и не подставлялся отдельным значением: он совпал с Z1 только из-за нулевой взаимности."
    )
    report["assumptions"].append("diagonal_from_positive_sequence")
    nodes: dict[str, ConnectivityNode] = {}
    level = VoltageLevel(id="vl_import", nominal_ll_v=base_kv * 1000.0)
    coordinates = side.get("coordinates") or {}

    def ensure_node(name: str) -> str:
        plain = name.split(".")[0]
        if plain not in nodes:
            xy = coordinates.get(plain) or [None, None]
            nodes[plain] = ConnectivityNode(
                id=plain,
                voltage_level_id=level.id,
                phases=["A", "B", "C"],
                x=None if xy[0] is None else float(xy[0]),
                y=None if xy[1] is None else float(xy[1]),
            )
        return plain

    source_bus = ensure_node(circuit["args"].get("bus1", "sourcebus"))
    source_args = dict(circuit["args"])
    for command in commands:
        if command["kind"] == "edit" and command["name"].lower() == "vsource.source":
            source_args.update(command["args"])
    r_ohm = float(source_args.get("r1", "nan"))
    x_ohm = float(source_args.get("x1", "nan"))
    if not math.isfinite(r_ohm) or not math.isfinite(x_ohm) or (r_ohm <= 0 and x_ohm <= 0):
        raise ValueError("у источника нет явных r1/x1; нулевой импеданс не подставляется")
    terminals: list[Terminal] = []
    lines: list[Line] = []
    switches: list[Switch] = []
    loads: list[Load] = []
    open_names = {item["name"].split(".", 1)[-1] for item in commands if item["kind"] == "open"}
    grounding = side.get("grounding") or {}
    switch_side = side.get("switches") or {}
    line_side = side.get("lines") or {}
    load_side = side.get("loads") or {}
    for command in commands:
        if command["kind"] != "new":
            continue
        klass, _, name = command["name"].partition(".")
        args = command["args"]
        if klass.lower() == "line":
            bus1 = ensure_node(args["bus1"])
            bus2 = ensure_node(args["bus2"])
            phases = int(float(args.get("phases", "3")))
            if phases != 3:
                raise ValueError(f"{name}: импортёр первой поставки принимает трёхфазные линии DSS, получено {phases}")
            length, unit = float(args.get("length", "1")), args.get("units", "km")
            length_m = length * (1000.0 if unit == "km" else 1.0 if unit == "m" else None)
            if length_m is None:
                raise ValueError(f"{name}: единица длины {unit} не поддержана")
            key = f"Line.{name}"
            if args.get("switch", "no").lower() in {"yes", "true", "y"}:
                meta = switch_side.get(key)
                if not meta:
                    raise ValueError(f"{key}: для switch=yes нужен вид аппарата в sidecar, разъединитель не равен выключателю")
                kind = meta["kind"]
                interrupt = meta.get("interrupt_current_a")
                if interrupt is None:
                    raise ValueError(f"{key}: не задан interrupt_current_a")
                left = f"{name}:{bus1}"
                right = f"{name}:{bus2}"
                terminals.extend(
                    [
                        Terminal(id=left, equipment_id=name, connectivity_node_id=bus1, phases=["A", "B", "C"]),
                        Terminal(id=right, equipment_id=name, connectivity_node_id=bus2, phases=["A", "B", "C"]),
                    ]
                )
                switches.append(
                    Switch(
                        id=name,
                        kind=kind,
                        terminal_from=left,
                        terminal_to=right,
                        phases=["A", "B", "C"],
                        closed=name not in open_names and str(meta.get("normally_closed", "true")).lower() != "false",
                        rated_current_a=float(meta.get("rated_current_a", 400)),
                        interrupt_current_a=float(interrupt),
                        operate_time_s=float(meta.get("operate_time_s", 0)),
                        locked=bool(meta.get("locked", False)),
                        operable=kind not in {"fuse", "bus_connector"},
                        provenance=Provenance(source=key, quality="derived", note="идеальный аппарат, r1/x1 DSS не используются"),
                    )
                )
                report["approximations"].append(f"{key}: импеданс switch=yes опущен, аппарат топологический")
                report["mappings"].append({"from": key, "to": f"switch:{name}", "kind": kind})
                continue
            meta = line_side.get(key) or {}
            if "rating_a" not in meta:
                raise ValueError(f"{key}: в sidecar нет rating_a")
            r1 = float(args.get("r1", "nan"))
            x1 = float(args.get("x1", "nan"))
            if not math.isfinite(r1) or not math.isfinite(x1):
                raise ValueError(f"{key}: нужны r1 и x1")
            matrix_r = [[r1 if i == j else 0.0 for j in range(3)] for i in range(3)]
            matrix_x = [[x1 if i == j else 0.0 for j in range(3)] for i in range(3)]
            left = f"{name}:{bus1}"
            right = f"{name}:{bus2}"
            terminals.extend(
                [
                    Terminal(id=left, equipment_id=name, connectivity_node_id=bus1, phases=["A", "B", "C"]),
                    Terminal(id=right, equipment_id=name, connectivity_node_id=bus2, phases=["A", "B", "C"]),
                ]
            )
            lines.append(
                Line(
                    id=name,
                    terminal_from=left,
                    terminal_to=right,
                    phases=["A", "B", "C"],
                    length_m=length_m,
                    r_ohm=matrix_r,
                    x_ohm=matrix_x,
                    b_siemens=[[0.0, 0.0, 0.0] for _ in range(3)],
                    rating_a=float(meta["rating_a"]),
                    emergency_rating_a=meta.get("emergency_rating_a"),
                    emergency_duration_s=meta.get("emergency_duration_s"),
                    provenance=Provenance(source=key, quality="derived", note="диагональ из r1/x1"),
                )
            )
            report["mappings"].append({"from": key, "to": f"line:{name}", "r1_ohm": r1, "x1_ohm": x1, "length_m": length_m})
        elif klass.lower() == "load":
            bus = ensure_node(args["bus1"])
            conn = args.get("conn", "")
            if conn not in {"wye", "delta"}:
                raise ValueError(f"Load.{name}: соединение {conn!r} не задано явно")
            phase_count = int(float(args.get("phases", "3")))
            bus_nodes = args["bus1"].split(".")[1:]
            phase_names = {"1": "A", "2": "B", "3": "C"}
            if phase_count == 1:
                if len(bus_nodes) != 1 or bus_nodes[0] not in phase_names:
                    raise ValueError(f"Load.{name}: однофазная нагрузка требует bus.1, bus.2 или bus.3")
                selected = [phase_names[bus_nodes[0]]]
            elif phase_count == 3:
                selected = ["A", "B", "C"]
            else:
                raise ValueError(f"Load.{name}: поддержаны 1 или 3 фазы, получено {phase_count}")
            ground_kind = (grounding.get(f"Load.{name}") or {}).get("kind") if isinstance(grounding.get(f"Load.{name}"), dict) else grounding.get(f"Load.{name}")
            if conn == "wye" and ground_kind not in {"solid", "ungrounded", "impedance"}:
                raise ValueError(f"Load.{name}: заземление звезды отсутствует в sidecar")
            if conn == "delta" and ground_kind not in {None, "not_applicable"}:
                raise ValueError(f"Load.{name}: у delta не должно быть заземления нейтрали")
            ground = GroundingSpec(kind="not_applicable" if conn == "delta" else ground_kind)
            kw = float(args.get("kw", "nan"))
            kvar = float(args.get("kvar", "0"))
            if not math.isfinite(kw):
                raise ValueError(f"Load.{name}: нет kw")
            meta = load_side.get(f"Load.{name}") or {}
            terminal = f"{name}:{bus}"
            terminals.append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=selected))  # type: ignore[arg-type]
            share = float(len(selected))
            loads.append(
                Load(
                    id=name,
                    terminal_id=terminal,
                    connection=conn,  # type: ignore[arg-type]
                    grounding=ground,
                    model="constant_pq",
                    phases=selected,  # type: ignore[arg-type]
                    p_w={phase: kw * 1000.0 / share for phase in selected},
                    q_var={phase: kvar * 1000.0 / share for phase in selected},
                    critical=bool(meta.get("critical", False)),
                    priority=int(meta.get("priority", 0)),
                    provenance=Provenance(source=f"Load.{name}", quality="nameplate"),
                )
            )
            report["mappings"].append({"from": f"Load.{name}", "to": f"load:{name}", "kw": kw, "kvar": kvar, "grounding": ground.kind})
    source_terminal = "source:import"
    terminals.append(Terminal(id=source_terminal, equipment_id="source", connectivity_node_id=source_bus, phases=["A", "B", "C"]))
    source = VoltageSource(
        id="source",
        terminal_id=source_terminal,
        ll_v=base_kv * 1000.0,
        angle_a_rad=math.radians(float(source_args.get("angle", 0.0))),
        r_ohm=r_ohm,
        x_ohm=x_ohm,
        provenance=Provenance(source="Vsource.source", quality="nameplate", note="r1/x1 прямой последовательности"),
    )
    meters = []
    for meter in side.get("meters") or []:
        ensure_node(meter["node"])
        meters.append(Meter(id=meter["id"], connectivity_node_id=meter["node"], delay_s=float(meter.get("delay_s", 0.0))))
    report["mappings"].append({"from": "Vsource.source", "to": "source", "r_ohm": r_ohm, "x_ohm": x_ohm})
    return Network(
        id=side.get("network_id", "imported"),
        name=side.get("name", circuit["name"].split(".", 1)[-1]),
        s_base_va=s_base,
        frequency_hz=frequency,
        voltage_levels=[level],
        connectivity_nodes=list(nodes.values()),
        terminals=terminals,
        lines=lines,
        switches=switches,
        loads=loads,
        sources=[source],
        meters=meters,
        assumptions=list(report["assumptions"]),
        provenance=Provenance(source=str(report["source"]), quality="derived"),
    )


def _parse_commands(text: str) -> list[dict[str, Any]]:
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.split("!")[0].split("//")[0].strip()
        if not line:
            continue
        if line.startswith("~"):
            buffer += " " + line[1:].strip()
            continue
        if buffer:
            logical.append(buffer)
        buffer = line
    if buffer:
        logical.append(buffer)
    commands = []
    for line in logical:
        parts = line.split()
        head = parts[0].lower()
        if head == "new" and len(parts) > 1 and parts[1].lower().startswith("circuit"):
            commands.append({"kind": "circuit", "name": parts[1], "args": _args(parts[2:])})
        elif head == "new":
            commands.append({"kind": "new", "name": parts[1], "args": _args(parts[2:])})
        elif head == "edit":
            commands.append({"kind": "edit", "name": parts[1], "args": _args(parts[2:])})
        elif head == "open":
            commands.append({"kind": "open", "name": parts[1], "args": {}})
        elif head in {"clear", "set", "calcvoltagebases", "solve"}:
            continue
        else:
            commands.append({"kind": "ignored", "name": line, "args": {}})
    return commands


def _args(tokens: list[str]) -> dict[str, str]:
    text = " ".join(tokens)
    found = {}
    # значения без пробелов
    for match in re.finditer(r"([A-Za-z0-9_]+)=\s*([^\s]+)", text):
        found[match.group(1).lower()] = match.group(2).strip("[]")
    return found

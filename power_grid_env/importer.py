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

from power_grid_env.dss_devices import attach_profile, loadshapes, try_device
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
    Transformer,
    VoltageLevel,
    VoltageSource,
    Winding,
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
    if source.suffix.lower() not in {".dss", ".txt"}:
        report["errors"].append("поддержаны только .dss + sidecar и канонический .json")
        return None, report
    side: dict[str, Any] = {}
    if sidecar is not None:
        side_path = Path(sidecar)
        if not side_path.exists():
            report["errors"].append("sidecar не найден")
            return None, report
        report["sidecar_sha256"] = file_sha256(side_path)
        side = yaml.safe_load(side_path.read_text(encoding="utf-8")) or {}
    else:
        report["approximations"].append(
            "sidecar нет: conn=wye в OpenDSS без узла нейтрали записывается как solid и попадает в отчёт"
        )
    try:
        text, sources = _read_dss_tree(source, [])
    except Exception as exc:
        report["errors"].append(str(exc))
        text, sources = source.read_text(encoding="utf-8"), []
    report["sources"] = sources
    try:
        network = _from_dss(text, side, report)
    except Exception as exc:
        report["errors"].append(str(exc))
        return None, report
    return network, report


def _add_transformer(name, args, raw, spec, ensure_node, nodes, terminals, transformers, report, strict: bool) -> None:
    phase_count = int(float(args.get("phases", "3")))
    windings = _bank_windings(raw)
    if len(windings) not in (2, 3):
        raise ValueError(f"Transformer.{name}: нужны две или три обмотки")
    vector_group = spec.get("vector_group")
    grounds = list(spec.get("grounding") or [])
    if not vector_group:
        if strict:
            raise ValueError(f"Transformer.{name}: в sidecar нет vector_group и заземлений")
        vector_group, grounds = _infer_group(name, windings, phase_count, report)
    if len(grounds) != len(windings):
        raise ValueError(f"Transformer.{name}: число заземлений не равно числу обмоток")
    xhl = float(args.get("xhl", "nan"))
    load_loss = float(args.get("loadloss", "nan"))
    if not math.isfinite(load_loss):
        percents = [float(item["r"]) for item in windings if "r" in item and _finite(item["r"])]
        if percents:
            load_loss = sum(percents)
            report["approximations"].append(f"Transformer.{name}: %loadloss собран из %r обмоток")
        else:
            # OpenDSS: %R по умолчанию 0.2 на обмотках 1 и 2, %loadloss = их сумма.
            load_loss = 0.4
            report["approximations"].append(
                f"Transformer.{name}: %r и %loadloss в файле нет, оставлен встроенный OpenDSS %loadloss=0.4"
            )
    if not math.isfinite(xhl):
        raise ValueError(f"Transformer.{name}: нужен xhl")
    xht = float(args["xht"]) if "xht" in args and len(windings) == 3 else None
    xlt = float(args["xlt"]) if "xlt" in args and len(windings) == 3 else None
    built: list[Winding] = []
    known_level = None
    for index, winding_args in enumerate(windings):
        bus_token = winding_args["bus"]
        bus_nodes = bus_token.split(".")[1:]
        phase = _phase_of(bus_token) if phase_count == 1 and len(bus_nodes) <= 1 else None
        bus = ensure_node(bus_token) if phase_count == 1 else ensure_node(bus_token, float(winding_args["kv"]))
        if phase_count == 1 and known_level is None and bus in nodes:
            known_level = nodes[bus].voltage_level_id
        elif phase_count == 1 and known_level is not None:
            nodes[bus].voltage_level_id = known_level
        connection = _conn_name(winding_args.get("conn", ""))
        if phase_count == 1:
            connection = "series"
            phase = phase or "A"
        ground_kind = grounds[index]
        terminal_phases = [phase] if phase else ["A", "B", "C"]
        terminal_id = f"{name}:w{index}:{bus}"
        terminals.append(Terminal(id=terminal_id, equipment_id=name, connectivity_node_id=bus, phases=terminal_phases))
        built.append(
            Winding(
                terminal_id=terminal_id,
                connection=connection,  # type: ignore[arg-type]
                phase=phase,
                nodes=bus_nodes or None,
                grounding=GroundingSpec(kind=ground_kind),
                nominal_ll_v=float(winding_args["kv"]) * 1000.0,
                rated_va=float(winding_args["kva"]) * 1000.0,
                r_pct=float(winding_args["r"]) if "r" in winding_args else None,
            )
        )
    transformers.append(
        Transformer(
            id=name,
            windings=built,
            phases=phase_count,
            vector_group=vector_group,
            x_hl_pct=xhl,
            x_ht_pct=xht,
            x_lt_pct=xlt,
            load_loss_pct=load_loss,
            no_load_loss_pct=float(args["noloadloss"]) if "noloadloss" in args else 0.0,
            imag_pct=0.0,
            provenance=Provenance(source=f"Transformer.{name}", quality="derived", note="группа и нейтраль взяты из conn DSS либо sidecar"),
        )
    )
    report["mappings"].append({"from": f"Transformer.{name}", "to": f"transformer:{name}", "vector_group": vector_group})


def _bank_windings(raw: str) -> list[dict[str, str]]:
    parsed = _windings(raw)
    if parsed and all("bus" in item and "kv" in item for item in parsed):
        return parsed
    buses = _bracket_list(raw, "buses")
    kvs = _bracket_list(raw, "kvs")
    kvas = _bracket_list(raw, "kvas")
    conns = _bracket_list(raw, "conns")
    if not (buses and kvs and kvas):
        return parsed
    return [
        {"bus": bus, "kv": kvs[index], "kva": kvas[index], "conn": conns[index] if conns else ""}
        for index, bus in enumerate(buses)
    ]


def _bracket_list(raw: str, key: str) -> list[str] | None:
    match = re.search(rf"(?i)\b{key}\s*=\s*(?:[\[(]([^\]\)]+)[\]\)]|[\"']([^\"']+)[\"'])", raw)
    if not match:
        return None
    body = match.group(1) if match.group(1) is not None else match.group(2)
    return [item for item in body.replace(",", " ").split() if item]


def _declared_voltage_bases_kv(commands: list[dict[str, Any]]) -> list[float]:
    found: list[float] = []
    for command in commands:
        if command.get("kind") != "directive" or "voltagebase" not in str(command.get("name", "")).lower():
            continue
        for token in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(command["name"])):
            value = float(token)
            if value > 0 and value not in found:
                found.append(value)
    return found


def _propagate_nominals(
    nodes, lines, transformers, terminals, levels, base_kv: float, voltage_bases_kv: list[float] | None = None
) -> None:
    """Узлы, созданные раньше линии, не должны навсегда остаться на базовом кВ цепи.

    Однофазный регулятор соединяет шины одного класса, но обрабатывается до линий.
    Номинал переносится по линиям и по регулятору, пока не закроется участок.
    """
    default = base_kv * 1000.0
    terminal_node = {item.id: item.connectivity_node_id for item in terminals}

    def nominal(node_id: str) -> float:
        return levels[nodes[node_id].voltage_level_id].nominal_ll_v

    def adopt_phase_voltage(node_id: str, phase_v: float) -> None:
        if voltage_bases_kv:
            target = min(voltage_bases_kv, key=lambda item: abs(item * 1000.0 / math.sqrt(3.0) - phase_v)) * 1000.0
        else:
            target = phase_v * math.sqrt(3.0)
        for level in levels.values():
            if abs(level.nominal_ll_v - target) <= 1.0:
                nodes[node_id].voltage_level_id = level.id
                return
        level_id = f"vl_phase_{phase_v:.4g}"
        levels[level_id] = VoltageLevel(id=level_id, nominal_ll_v=target)
        nodes[node_id].voltage_level_id = level_id

    # Обмотка 120 В на шине, созданной с номиналом фидера, иначе о.е. считаются от 7.2 кВ.
    for transformer in transformers:
        if transformer.phases != 1:
            continue
        for winding in transformer.windings:
            bus = terminal_node[winding.terminal_id]
            if winding.nominal_ll_v + 1.0 < nominal(bus) / math.sqrt(3.0) * 0.5:
                adopt_phase_voltage(bus, winding.nominal_ll_v)

    def is_default(volts: float) -> bool:
        return abs(volts - default) <= 1.0

    changed = True
    while changed:
        changed = False
        for line in lines:
            left = terminal_node[line.terminal_from]
            right = terminal_node[line.terminal_to]
            left_v, right_v = nominal(left), nominal(right)
            if abs(left_v - right_v) <= 1.0:
                continue
            if is_default(left_v) and not is_default(right_v):
                nodes[left].voltage_level_id = nodes[right].voltage_level_id
                changed = True
            elif is_default(right_v) and not is_default(left_v):
                nodes[right].voltage_level_id = nodes[left].voltage_level_id
                changed = True
        for transformer in transformers:
            winding_kv = {round(winding.nominal_ll_v, 1) for winding in transformer.windings}
            if len(winding_kv) != 1:
                continue
            buses = [terminal_node[winding.terminal_id] for winding in transformer.windings]
            volts = [nominal(bus) for bus in buses]
            known = [volts[index] for index, volts_value in enumerate(volts) if not is_default(volts_value)]
            if not known or any(abs(item - known[0]) > 1.0 for item in known):
                continue
            for bus, volts_value in zip(buses, volts):
                if is_default(volts_value):
                    nodes[bus].voltage_level_id = nodes[buses[volts.index(known[0])]].voltage_level_id
                    changed = True


def _case_insensitive(path: Path) -> Path:
    """Redirect в OpenDSS не зависит от регистра, на Linux путь — зависит."""
    parent = path.parent
    if not parent.exists():
        return path
    matches = [item for item in parent.iterdir() if item.name.lower() == path.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return path


def _infer_group(name: str, windings: list[dict[str, str]], phase_count: int, report: dict[str, Any]) -> tuple[str, list[str]]:
    if phase_count == 1:
        if len(windings) == 2:
            report["approximations"].append(f"Transformer.{name}: однофазный аппарат записан как Ii0, без группы Dyn")
            return "Ii0", ["not_applicable", "not_applicable"]
        if len(windings) == 3:
            report["approximations"].append(
                f"Transformer.{name}: трёхобмоточный однофазный аппарат со средней точкой записан как Iii0"
            )
            return "Iii0", ["not_applicable", "not_applicable", "not_applicable"]
        raise ValueError(f"Transformer.{name}: для одной фазы поддержаны 2 или 3 обмотки")
    connections = []
    grounds = []
    for index, winding in enumerate(windings):
        connection = _conn_name(winding.get("conn", ""))
        connections.append(connection)
        if connection == "delta":
            grounds.append("not_applicable")
        elif connection == "wye":
            grounds.append("solid")
            report["approximations"].append(
                f"Transformer.{name}: обмотка {index} conn=wye без узла нейтрали. "
                "В OpenDSS нейтраль сидит на земле, здесь это записано как solid."
            )
        else:
            raise ValueError(f"Transformer.{name}: соединение обмотки {index} не задано")
    group = _group_text(connections, grounds)
    if len(set(connections)) > 1:
        report["approximations"].append(
            f"Transformer.{name}: сдвиг {group} — стандартный ANSI OpenDSS для смешанных обмоток, в DSS часы не написаны"
        )
    return group, grounds


def _group_text(connections: list[str], grounds: list[str]) -> str:
    def token(connection: str, ground: str, secondary: bool) -> str:
        if connection == "delta":
            return "d" if secondary else "D"
        letter = "y" if secondary else "Y"
        if ground == "solid":
            letter += "n" if secondary else "N"
        return letter

    clock = "0" if len(set(connections)) == 1 else "1"
    if len(connections) == 2:
        return token(connections[0], grounds[0], False) + token(connections[1], grounds[1], True) + clock
    second = "0" if connections[2] == connections[0] else clock
    return (
        token(connections[0], grounds[0], False)
        + token(connections[1], grounds[1], True)
        + ("0" if connections[1] == connections[0] else "1")
        + token(connections[2], grounds[2], True)
        + second
    )


def _conn_name(value: str) -> str:
    name = value.lower()
    if name in {"wye", "y", "ln"}:
        return "wye"
    if name in {"delta", "d", "ll"}:
        return "delta"
    return ""


def _phase_of(bus: str) -> str | None:
    nodes = bus.split(".")[1:]
    mapping = {"1": "A", "2": "B", "3": "C"}
    if len(nodes) == 1 and nodes[0] in mapping:
        return mapping[nodes[0]]
    return None


def _infer_interpretation(text: str, strict: bool) -> str:
    lowered = text.lower()
    if "rmatrix" in lowered:
        return "explicit_phase_matrix"
    if re.search(r"\br0\s*=", lowered) or re.search(r"\bx0\s*=", lowered):
        return "sequence_to_phase"
    if strict:
        raise ValueError(
            "r1/x1 не определяют фазную матрицу. В sidecar нужно явно указать "
            "impedance_interpretation: diagonal_from_positive_sequence"
        )
    raise ValueError("в DSS нет ни матрицы, ни r0/x0, а sidecar не задаёт смысл r1/x1")


def _finite(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _expand_like(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenDSS like= копирует ранее созданный объект и заменяет перечисленные свойства."""
    known: dict[str, dict[str, Any]] = {}
    expanded = []
    for command in commands:
        if command["kind"] == "new" and command.get("args", {}).get("like"):
            source_name = command["args"]["like"].lower()
            source = known.get(source_name)
            if source is None:
                raise ValueError(f"{command['name']}: like={command['args']['like']} не найден")
            merged_args = dict(source.get("args") or {})
            merged_args.update(command["args"])
            merged_args.pop("like", None)
            command = {
                **command,
                "args": merged_args,
                "raw": _overlay_like(source.get("raw") or "", command.get("raw") or "", command["name"]),
            }
        expanded.append(command)
        if command["kind"] == "new":
            short = command["name"].split(".", 1)[-1].lower()
            known[short] = command
            known[command["name"].lower()] = command
    return expanded


def _overlay_like(source_raw: str, new_line: str, new_name: str) -> str:
    text = re.sub(r"(?i)^new\s+(\w+)\.\S+", lambda match: f"New {match.group(1)}.{new_name.split('.', 1)[-1]}", source_raw, count=1)
    for match in re.finditer(r"(?i)([A-Za-z%][A-Za-z0-9_]*)\s*=\s*(\[[^\]]*\]|\([^)]*\)|\"[^\"]*\"|'[^']*'|\S+)", new_line):
        key, value = match.group(1), match.group(2)
        if key.lower() == "like":
            continue
        pattern = rf"(?i)\b{re.escape(key)}\s*=\s*(\[[^\]]*\]|\([^)]*\)|\"[^\"]*\"|'[^']*'|\S+)"
        text, count = re.subn(pattern, f"{key}={value}", text, count=1)
        if count == 0:
            text += f" {key}={value}"
    return text


def _from_dss(text: str, side: dict[str, Any], report: dict[str, Any]) -> Network:
    commands = _expand_like(_parse_commands(text))
    report["commands"] = _command_ledger(commands)
    unsupported = [row for row in report["commands"] if row["status"] == "unsupported"]
    if unsupported:
        raise ValueError(
            "не перенесены команды: " + "; ".join(row["command"] for row in unsupported)
        )
    circuit = next((item for item in commands if item["kind"] == "circuit"), None)
    if circuit is None:
        edit = next((item for item in commands if item["kind"] == "edit" and "vsource" in item["name"].lower()), None)
        if edit is None or not side.get("schema_version"):
            if edit is None:
                raise ValueError("в DSS нет New Circuit")
        base_kv_edit = float((edit or {}).get("args", {}).get("basekv", "0"))
        circuit = {"name": "Circuit.derived", "args": {"basekv": str(base_kv_edit), "bus1": "SourceBus", "pu": (edit or {}).get("args", {}).get("pu", "1")}}
        report["approximations"].append("New Circuit в файле нет. Цепь собрана из Edit Vsource, шина SourceBus.")
    base_kv = float(circuit["args"].get("basekv", "0"))
    if base_kv <= 0:
        raise ValueError("Circuit.basekv не задан")
    if "frequency_hz" in side:
        frequency = float(side["frequency_hz"])
    else:
        lowered = text.lower().replace(" ", "")
        if "basefreq=50" in lowered or "defaultbasefrequency=50" in lowered:
            frequency = 50.0
        else:
            frequency = 60.0
            if "basefreq=60" not in lowered and "defaultbasefrequency=60" not in lowered:
                report["approximations"].append("частота в DSS не задана: оставлен встроенный OpenDSS 60 Гц")
    s_base = float(side.get("s_base_va", 0))
    if s_base <= 0:
        if side.get("schema_version"):
            raise ValueError("sidecar.s_base_va обязателен")
        s_base = 10_000_000.0
        report["approximations"].append("s_base_va=10 МВА — только отчётная база, омы линий не масштабируются")
    interpretation = side.get("impedance_interpretation") or _infer_interpretation(text, bool(side.get("schema_version")))
    if interpretation == "diagonal_from_positive_sequence":
        report["approximations"].append(
            "Последовательные r1/x1 переведены в диагональную фазную матрицу без взаимоиндукции. "
            "Z0 не измерялся и не подставлялся отдельным значением: он совпал с Z1 только из-за нулевой взаимности."
        )
        report["assumptions"].append("diagonal_from_positive_sequence")
    elif interpretation == "explicit_phase_matrix":
        report["assumptions"].append("explicit_phase_matrix")
    elif interpretation == "sequence_to_phase":
        report["assumptions"].append("sequence_to_phase")
        report["approximations"].append(
            "Z0 и Z1 переведены в фазную матрицу транспонированной линии: "
            "Zс=(2Z1+Z0)/3, Zв=(Z0-Z1)/3. Это не измеренная матрица проводников."
        )
    else:
        raise ValueError(
            "sidecar.impedance_interpretation должен быть diagonal_from_positive_sequence, "
            "explicit_phase_matrix или sequence_to_phase"
        )
    nodes: dict[str, ConnectivityNode] = {}
    levels: dict[str, VoltageLevel] = {}
    coordinates = side.get("coordinates") or {}

    def level_for(volts: float) -> str:
        key = f"vl_{round(volts, 3)}"
        if key not in levels:
            levels[key] = VoltageLevel(id=key, nominal_ll_v=volts)
        return key

    def ensure_node(name: str, kv_ll: float | None = None) -> str:
        plain = name.split(".")[0]
        if plain in nodes:
            if kv_ll is not None:
                volts = float(kv_ll) * 1000.0
                current = levels[nodes[plain].voltage_level_id].nominal_ll_v
                if abs(current - volts) > 1.0:
                    if abs(current - base_kv * 1000.0) <= 1.0:
                        nodes[plain].voltage_level_id = level_for(volts)
                    else:
                        raise ValueError(f"{plain}: конфликт номиналов {current} В и {volts} В")
            return plain
        volts = base_kv * 1000.0 if kv_ll is None else float(kv_ll) * 1000.0
        xy = coordinates.get(plain) or [None, None]
        nodes[plain] = ConnectivityNode(
            id=plain,
            voltage_level_id=level_for(volts),
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
    if not math.isfinite(r_ohm) or not math.isfinite(x_ohm):
        mva_sc = float(source_args.get("mvasc3", "nan"))
        isc3 = float(source_args.get("isc3", "nan"))
        if math.isfinite(mva_sc) and mva_sc > 0:
            impedance = (base_kv ** 2) / mva_sc
            label = f"MVAsc3={mva_sc:g} МВА"
        elif math.isfinite(isc3) and isc3 > 0:
            impedance = (base_kv * 1000.0) / (math.sqrt(3.0) * isc3)
            label = f"ISC3={isc3:g} А"
        else:
            raise ValueError("у источника нет явных r1/x1; нулевой импеданс не подставляется")
        x_over_r = float(source_args.get("x1r1", "4"))
        r_ohm = impedance / math.sqrt(1.0 + x_over_r * x_over_r)
        x_ohm = r_ohm * x_over_r
        report["approximations"].append(
            f"Vsource.source: r1/x1 получены из {label} и X/R={x_over_r:g}."
        )
    r0_ohm, x0_ohm = _source_zero_sequence(base_kv, r_ohm, x_ohm, source_args, report)
    if r_ohm <= 0 and x_ohm <= 0:
        raise ValueError("у источника нет явных r1/x1; нулевой импеданс не подставляется")
    terminals: list[Terminal] = []
    lines: list[Line] = []
    switches: list[Switch] = []
    loads: list[Load] = []
    transformers: list[Transformer] = []
    generators: list = []
    shunts: list = []
    storages: list = []
    inverters: list = []
    extra_sources: list[VoltageSource] = []
    shapes = loadshapes(commands)
    property_edits: dict[str, dict[str, str]] = {}
    for command in commands:
        if command["kind"] == "edit":
            property_edits.setdefault(command["name"].lower(), {}).update(command["args"])
    open_names = {item["name"].split(".", 1)[-1] for item in commands if item["kind"] == "open"}
    grounding = side.get("grounding") or {}
    switch_side = side.get("switches") or {}
    line_side = side.get("lines") or {}
    load_side = side.get("loads") or {}
    transformer_side = side.get("transformers") or {}
    linecodes = _linecodes(commands)
    ordered = sorted(
        [command for command in commands if command["kind"] == "new"],
        key=lambda command: 0 if command["name"].lower().startswith("transformer.") else 1,
    )
    for command in ordered:
        klass, _, name = command["name"].partition(".")
        args = command["args"]
        if klass.lower() == "linecode":
            continue
        if klass.lower() == "fuse":
            report["approximations"].append(
                f"Fuse.{name}: контроллер предохранителя не входит в расчёт режима. "
                f"Наблюдаемый объект {args.get('monitoredobj', 'не указан')} остаётся в схеме."
            )
            continue
        if try_device(
            klass,
            name,
            args,
            command.get("raw") or "",
            {
                "ensure_node": ensure_node,
                "grounding": grounding,
                "side": side,
                "report": report,
                "terminals": terminals,
                "shunts": shunts,
                "generators": generators,
                "storages": storages,
                "inverters": inverters,
                "sources": extra_sources,
                "transformers": transformers,
            },
        ):
            continue
        if klass.lower() == "transformer":
            _add_transformer(
                name,
                args,
                command.get("raw") or "",
                transformer_side.get(f"Transformer.{name}") or {},
                ensure_node,
                nodes,
                terminals,
                transformers,
                report,
                bool(side.get("schema_version")),
            )
            continue
        if klass.lower() == "line":
            bus1 = ensure_node(args["bus1"])
            bus2 = ensure_node(args["bus2"])
            level1 = levels[nodes[bus1].voltage_level_id].nominal_ll_v
            level2 = levels[nodes[bus2].voltage_level_id].nominal_ll_v
            if abs(level1 - level2) > 1.0:
                default = base_kv * 1000.0
                if abs(level1 - default) <= 1.0 and abs(level2 - default) > 1.0:
                    nodes[bus1].voltage_level_id = nodes[bus2].voltage_level_id
                elif abs(level2 - default) <= 1.0 and abs(level1 - default) > 1.0:
                    nodes[bus2].voltage_level_id = nodes[bus1].voltage_level_id
                else:
                    raise ValueError(f"Line.{name}: линия соединяет {level1} В и {level2} В")
            phase_count = int(float(args.get("phases", "3")))
            selected = _line_phases(args, phase_count, name)
            _widen_neutral(nodes[bus1], selected)
            _widen_neutral(nodes[bus2], selected)
            is_switch = args.get("switch", "no").lower() in {"yes", "true", "y"}
            # Switch=yes без length получает 0.001 в единицах OpenDSS, не 1.
            # Иначе r1=1e-4 превращается в 1e-4 Ом вместо 1e-7 Ом.
            length = float(args["length"]) if "length" in args else (0.001 if is_switch else 1.0)
            unit = args.get("units", "km").lower()
            if unit not in _LENGTH_TO_M:
                raise ValueError(f"{name}: единица длины {unit} не поддержана")
            length_m = length * _LENGTH_TO_M[unit]
            key = f"Line.{name}"
            enabled = args.get("enabled", "yes").lower() not in {"n", "no", "false", "0"}
            # В чужом DSS switch=yes с linecode остаётся ветвью с импедансом.
            # Топологический аппарат — только для authored sidecar, где шины сливаются.
            keep_impedance = is_switch and not side.get("schema_version") and bool(args.get("linecode") or "r1" in args or "rmatrix" in (command.get("raw") or "").lower())
            if not enabled and (not is_switch or keep_impedance):
                report["approximations"].append(f"{key}: enabled=no, линия выведена из расчёта")
                continue
            if is_switch and not keep_impedance:
                meta = switch_side.get(key)
                if not meta:
                    if side.get("schema_version"):
                        raise ValueError(f"{key}: для switch=yes нужен вид аппарата в sidecar, разъединитель не равен выключателю")
                    meta = {"kind": "breaker", "interrupt_current_a": 10000, "rated_current_a": 400}
                    report["approximations"].append(
                        f"{key}: Switch=yes без sidecar принят выключателем. Импеданс DSS опущен, это не разъединитель."
                    )
                kind = meta["kind"]
                interrupt = meta.get("interrupt_current_a")
                if interrupt is None:
                    raise ValueError(f"{key}: не задан interrupt_current_a")
                left = f"{name}:{bus1}"
                right = f"{name}:{bus2}"
                terminals.extend(
                    [
                        Terminal(id=left, equipment_id=name, connectivity_node_id=bus1, phases=selected),
                        Terminal(id=right, equipment_id=name, connectivity_node_id=bus2, phases=selected),
                    ]
                )
                switches.append(
                    Switch(
                        id=name,
                        kind=kind,
                        terminal_from=left,
                        terminal_to=right,
                        phases=selected,
                        closed=enabled and name not in open_names and str(meta.get("normally_closed", "true")).lower() != "false",
                        rated_current_a=float(meta.get("rated_current_a", 400)),
                        interrupt_current_a=float(interrupt),
                        operate_time_s=float(meta.get("operate_time_s", 0)),
                        locked=bool(meta.get("locked", False)),
                        operable=kind not in {"fuse", "bus_connector"},
                        provenance=Provenance(source=key, quality="derived", note="идеальный аппарат, r1/x1 DSS не используются"),
                    )
                )
                if not enabled:
                    report["approximations"].append(f"{key}: enabled=no, аппарат разомкнут и шины не объединяет")
                report["approximations"].append(f"{key}: импеданс switch=yes опущен, аппарат топологический")
                report["mappings"].append({"from": key, "to": f"switch:{name}", "kind": kind})
                continue
            meta = line_side.get(key) or {}
            if "rating_a" not in meta:
                if side.get("schema_version"):
                    raise ValueError(f"{key}: в sidecar нет rating_a")
                meta = {**meta, "rating_a": 400.0}
                report["approximations"].append(f"{key}: rating_a не задан, для проверки режима взят 400 А")
            raw = command.get("raw") or ""
            if is_switch and phase_count != 1 and args.get("linecode"):
                # Switch=yes оставляет флаг симметричных составляющих. На сборке Y
                # OpenDSS заменяет матрицу на r1=x1=r0=x0=1 и length=0.001.
                size = len(selected)
                matrix_r = [[0.001 if i == j else 0.0 for j in range(size)] for i in range(size)]
                matrix_x = [[0.001 if i == j else 0.0 for j in range(size)] for i in range(size)]
                matrix_b = [[0.0 for _ in range(size)] for _ in range(size)]
                impedance_note = "Switch=yes: Z1=Z0=0.001+j0.001 Ом, как подставляет OpenDSS вместо linecode"
            else:
                matrix_r, matrix_x, matrix_b, impedance_note = _line_impedance(
                key,
                args,
                raw,
                length,
                unit,
                selected,
                linecodes,
                interpretation,
                frequency,
                per_length=not bool(side.get("schema_version")),
            )
            left = f"{name}:{bus1}"
            right = f"{name}:{bus2}"
            terminals.extend(
                [
                    Terminal(id=left, equipment_id=name, connectivity_node_id=bus1, phases=selected),
                    Terminal(id=right, equipment_id=name, connectivity_node_id=bus2, phases=selected),
                ]
            )
            lines.append(
                Line(
                    id=name,
                    terminal_from=left,
                    terminal_to=right,
                    phases=selected,
                    length_m=length_m,
                    r_ohm=matrix_r,
                    x_ohm=matrix_x,
                    b_siemens=matrix_b,
                    rating_a=float(meta["rating_a"]),
                    emergency_rating_a=meta.get("emergency_rating_a"),
                    emergency_duration_s=meta.get("emergency_duration_s"),
                    provenance=Provenance(source=key, quality="derived", note=impedance_note),
                )
            )
            report["mappings"].append(
                {
                    "from": key,
                    "to": f"line:{name}",
                    "r_ohm": matrix_r[0][0],
                    "x_ohm": matrix_x[0][0],
                    "length_m": length_m,
                    "note": impedance_note,
                }
            )
        elif klass.lower() == "load":
            bus = ensure_node(args["bus1"])
            conn = args.get("conn", "").lower()
            if conn in {"wye", "y"}:
                conn = "wye"
            elif conn in {"delta", "d"}:
                conn = "delta"
            elif not side.get("schema_version"):
                conn = "wye"
                report["approximations"].append(f"Load.{name}: conn не задан, принят wye как в OpenDSS по умолчанию")
            else:
                raise ValueError(f"Load.{name}: соединение {conn!r} не задано явно")
            phase_count = int(float(args.get("phases", "3")))
            bus_nodes = args["bus1"].split(".")[1:]
            phase_names = {"1": "A", "2": "B", "3": "C"}
            if phase_count == 1 and conn == "delta" and len(bus_nodes) == 1:
                report["approximations"].append(
                    f"Load.{name}: delta с одним узлом в OpenDSS включён между этой фазой и землёй, записан как wye с тем же kV"
                )
                conn = "wye"
            if phase_count == 1 and len(bus_nodes) == 2 and conn == "delta":
                selected = [phase_names[node] for node in bus_nodes]
            elif phase_count == 1:
                if len(bus_nodes) != 1 or bus_nodes[0] not in phase_names:
                    raise ValueError(f"Load.{name}: однофазная нагрузка требует bus.1, bus.2 или bus.3")
                selected = [phase_names[bus_nodes[0]]]
            elif phase_count == 3:
                selected = ["A", "B", "C"]
            else:
                raise ValueError(f"Load.{name}: поддержаны 1 или 3 фазы, получено {phase_count}")
            ground_kind = (grounding.get(f"Load.{name}") or {}).get("kind") if isinstance(grounding.get(f"Load.{name}"), dict) else grounding.get(f"Load.{name}")
            if conn == "wye" and ground_kind not in {"solid", "ungrounded", "impedance"}:
                if side.get("schema_version"):
                    raise ValueError(f"Load.{name}: заземление звезды отсутствует в sidecar")
                ground_kind = "solid"
                report["approximations"].append(f"Load.{name}: звезда без sidecar записана как solid, нейтраль OpenDSS на земле")
            if conn == "delta" and ground_kind not in {None, "not_applicable"}:
                raise ValueError(f"Load.{name}: у delta не должно быть заземления нейтрали")
            ground = GroundingSpec(kind="not_applicable" if conn == "delta" else ground_kind)
            kw = float(args.get("kw", "nan"))
            if "pf" in args and "kvar" not in args:
                pf = float(args["pf"])
                kvar = 0.0 if abs(pf) >= 1 else math.copysign(kw * math.tan(math.acos(min(1.0, abs(pf)))), pf)
            else:
                kvar = float(args.get("kvar", "0"))
            if not math.isfinite(kw):
                raise ValueError(f"Load.{name}: нет kw")
            model_code = args.get("model", "1")
            load_model = {"1": "constant_pq", "2": "constant_z", "3": "zip", "4": "zip", "5": "constant_i"}.get(model_code)
            if load_model is None:
                raise ValueError(f"Load.{name}: model={model_code} не поддержан")
            zip_terms = {
                "3": {"zip_z": 0.0, "zip_i": 0.0, "zip_p": 1.0, "zip_zq": 1.0, "zip_iq": 0.0, "zip_pq": 0.0},
                "4": {"zip_z": 0.0, "zip_i": 1.0, "zip_p": 0.0, "zip_zq": 1.0, "zip_iq": 0.0, "zip_pq": 0.0},
            }.get(model_code, {})
            edited = property_edits.get(f"load.{name}".lower(), {})
            # Без sidecar оставляем пороги OpenDSS: ниже vminpu и выше vmaxpu модель становится Z.
            vmin_default = "0" if side.get("schema_version") else "0.95"
            vmax_default = "1.5" if side.get("schema_version") else "1.05"
            vmin = float(edited.get("vminpu", args.get("vminpu", vmin_default)))
            vmax = float(edited.get("vmaxpu", args.get("vmaxpu", vmax_default)))
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
                    model=load_model,  # type: ignore[arg-type]
                    phases=selected,  # type: ignore[arg-type]
                    p_w={phase: kw * 1000.0 / share for phase in selected},
                    q_var={phase: kvar * 1000.0 / share for phase in selected},
                    rated_kv=float(args["kv"]) if "kv" in args else None,
                    vmin_pu=vmin,
                    vmax_pu=vmax,
                    opendss_model=int(model_code),
                    **zip_terms,
                    critical=bool(meta.get("critical", False)),
                    priority=int(meta.get("priority", 0)),
                    provenance=Provenance(source=f"Load.{name}", quality="nameplate"),
                )
            )
            report["mappings"].append({"from": f"Load.{name}", "to": f"load:{name}", "kw": kw, "kvar": kvar, "grounding": ground.kind})
            attach_profile(name, args, shapes, report)
    source_terminal = "source:import"
    terminals.append(Terminal(id=source_terminal, equipment_id="source", connectivity_node_id=source_bus, phases=["A", "B", "C"]))
    source = VoltageSource(
        id="source",
        terminal_id=source_terminal,
        ll_v=base_kv * 1000.0 * float(source_args.get("pu", circuit["args"].get("pu", 1.0))),
        angle_a_rad=math.radians(float(source_args.get("angle", 0.0))),
        r_ohm=r_ohm,
        x_ohm=x_ohm,
        r0_ohm=r0_ohm,
        x0_ohm=x0_ohm,
        provenance=Provenance(source="Vsource.source", quality="nameplate", note="r1/x1 прямой последовательности"),
    )
    meters = []
    for meter in side.get("meters") or []:
        ensure_node(meter["node"])
        meters.append(Meter(id=meter["id"], connectivity_node_id=meter["node"], delay_s=float(meter.get("delay_s", 0.0))))
    report["mappings"].append({"from": "Vsource.source", "to": "source", "r_ohm": r_ohm, "x_ohm": x_ohm})
    _propagate_nominals(
        nodes,
        [*lines, *switches],
        transformers,
        terminals,
        levels,
        base_kv,
        _declared_voltage_bases_kv(commands),
    )
    _apply_bus_coords(commands, nodes, Path(str(report.get("source") or ".")).parent, report)
    return Network(
        id=side.get("network_id", "imported"),
        name=side.get("name", circuit["name"].split(".", 1)[-1]),
        s_base_va=s_base,
        frequency_hz=frequency,
        voltage_levels=list(levels.values()),
        connectivity_nodes=list(nodes.values()),
        terminals=terminals,
        lines=lines,
        switches=switches,
        loads=loads,
        transformers=transformers,
        sources=[source, *extra_sources],
        generators=generators,
        inverters=inverters,
        storages=storages,
        shunts=shunts,
        meters=meters,
        assumptions=list(report["assumptions"]),
        provenance=Provenance(source=str(report["source"]), quality="derived"),
    )


def _read_dss_tree(path: Path, stack: list[Path]) -> tuple[str, list[dict[str, str]]]:
    resolved = path.resolve()
    if resolved in stack:
        raise ValueError(f"цикл Redirect: {resolved.name}")
    if not resolved.exists():
        resolved = _case_insensitive(resolved)
    if not resolved.exists():
        raise ValueError(f"файл DSS не найден: {resolved}")
    sources = [{"path": str(resolved), "sha256": file_sha256(resolved)}]
    pieces: list[str] = []
    for raw in resolved.read_text(encoding="utf-8").splitlines():
        code = raw.split("!")[0].split("//")[0].strip()
        if code.lower().startswith("redirect "):
            target = code.split(None, 1)[1].strip().strip('"')
            child_text, child_sources = _read_dss_tree(resolved.parent / target, stack + [resolved])
            pieces.append(child_text)
            sources.extend(child_sources)
            continue
        pieces.append(raw)
    return "\n".join(pieces), sources


def _source_zero_sequence(
    base_kv: float,
    r1: float,
    x1: float,
    source_args: dict[str, str],
    report: dict[str, Any],
) -> tuple[float | None, float | None]:
    """Z0 по той же квадратичной формуле, что OpenDSS использует для MVAsc1 и ISC1."""
    explicit_r = float(source_args.get("r0", "nan"))
    explicit_x = float(source_args.get("x0", "nan"))
    if math.isfinite(explicit_r) and math.isfinite(explicit_x):
        return explicit_r, explicit_x
    mva1 = float(source_args.get("mvasc1", "nan"))
    isc1 = float(source_args.get("isc1", "nan"))
    factor = math.sqrt(3.0)
    if math.isfinite(mva1) and mva1 > 0:
        isc1 = mva1 * 1000.0 / (factor * base_kv)
        label = f"MVAsc1={mva1:g} МВА"
    elif math.isfinite(isc1) and isc1 > 0:
        label = f"ISC1={isc1:g} А"
    elif "mvasc3" in source_args and "r1" not in source_args and "mvasc1" not in source_args:
        mva1 = 2100.0
        isc1 = mva1 * 1000.0 / (factor * base_kv)
        label = "MVAsc1=2100 МВА, встроенное значение OpenDSS: в файле задан только MVAsc3"
    else:
        if "mvasc3" in source_args or "isc3" in source_args:
            report["approximations"].append("Vsource.source: Z0 не задан, в повторный расчёт уходит только Z1")
        return None, None
    x0_over_r0 = float(source_args.get("x0r0", "3"))
    a = 1.0 + x0_over_r0 * x0_over_r0
    b = 4.0 * (r1 + x1 * x0_over_r0)
    c = 4.0 * (r1 * r1 + x1 * x1) - (3.0 * base_kv * 1000.0 / factor / isc1) ** 2
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0 or isc1 <= 0:
        raise ValueError("Vsource.source: Z0 из MVAsc1/ISC1 не вычисляется")
    r0 = (-b + math.sqrt(discriminant)) / (2.0 * a)
    if r0 < 0:
        r0 = (-b - math.sqrt(discriminant)) / (2.0 * a)
    report["approximations"].append(
        f"Vsource.source: r0/x0 получены из {label} и X0/R0={x0_over_r0:g} по формуле OpenDSS."
    )
    return r0, r0 * x0_over_r0


def _line_phases(args: dict[str, str], count: int, name: str) -> list[str]:
    if count == 3:
        return ["A", "B", "C"]
    if count == 4:
        return ["A", "B", "C", "N"]
    if count == 2:
        mapping = {"1": "A", "2": "B", "3": "C", "4": "N"}
        left = args["bus1"].split(".")[1:]
        right = args["bus2"].split(".")[1:]
        if left != right or len(left) != 2 or any(node not in mapping for node in left):
            raise ValueError(f"Line.{name}: двухфазная линия требует одинаковые bus.i.j на обоих концах")
        return [mapping[node] for node in left]
    if count == 1:
        mapping = {"1": "A", "2": "B", "3": "C", "4": "N"}
        left = args["bus1"].split(".")[1:]
        right = args["bus2"].split(".")[1:]
        if len(left) != 1 or left != right or left[0] not in mapping:
            raise ValueError(f"Line.{name}: однофазная линия требует одинаковые bus.1 … bus.4 на обоих концах")
        return [mapping[left[0]]]
    raise ValueError(f"Line.{name}: поддержаны 1, 3 или 4 фазы, получено {count}")


def _widen_neutral(node: ConnectivityNode, phases: list[str]) -> None:
    if "N" not in phases or "N" in node.phases:
        return
    node.phases = [phase for phase in ("A", "B", "C", "N") if phase in set(node.phases) | {"N"}]


def _line_impedance(
    key: str,
    args: dict[str, str],
    raw: str,
    length: float,
    unit: str,
    selected: list[str],
    linecodes: dict[str, dict[str, float | str]],
    interpretation: str,
    frequency: float,
    per_length: bool = False,
) -> tuple[list[list[float]], list[list[float]], list[list[float]], str]:
    r_matrix = _triangle(raw, "rmatrix")
    x_matrix = _triangle(raw, "xmatrix")
    if r_matrix is not None or x_matrix is not None:
        if interpretation != "explicit_phase_matrix":
            raise ValueError(f"{key}: rmatrix требует impedance_interpretation: explicit_phase_matrix")
        if r_matrix is None or x_matrix is None:
            raise ValueError(f"{key}: rmatrix и xmatrix задаются вместе")
        if args.get("linecode") or "r1" in args or "x1" in args:
            raise ValueError(f"{key}: rmatrix не смешивается с r1/x1 или linecode")
        matrix_r = _scale_matrix(r_matrix, length)
        matrix_x = _scale_matrix(x_matrix, length)
        if len(matrix_r) != len(selected) or len(matrix_x) != len(selected):
            raise ValueError(f"{key}: размер матрицы не равен числу фаз")
        c_matrix = _triangle(raw, "cmatrix")
        if c_matrix is None:
            matrix_b = [[0.0 for _ in selected] for _ in selected]
        else:
            if len(c_matrix) != len(selected):
                raise ValueError(f"{key}: cmatrix не совпадает с числом фаз")
            omega = 2.0 * math.pi * frequency
            matrix_b = _scale_matrix(c_matrix, length * 1e-9 * omega)
        return matrix_r, matrix_x, matrix_b, "rmatrix/xmatrix умножены на длину в единицах линии"
    code_name = args.get("linecode")
    if code_name and ("r1" in args or "x1" in args):
        raise ValueError(f"{key}: заданы и linecode, и r1/x1; оставьте один источник импеданса")
    if code_name:
        code = linecodes.get(code_name.lower())
        if code is None:
            raise ValueError(f"{key}: LineCode.{code_name} не объявлен")
        span = _length_in(length, unit, str(code["units"]))
        if code["kind"] == "matrix":
            if interpretation != "explicit_phase_matrix":
                raise ValueError(f"{key}: матричный linecode требует explicit_phase_matrix")
            if len(code["r"]) != len(selected) or len(code["x"]) != len(selected):
                raise ValueError(f"{key}: матрица linecode не равна числу фаз")
            matrix_r = _scale_matrix(code["r"], span)
            matrix_x = _scale_matrix(code["x"], span)
            matrix_b = _linecode_susceptance(code.get("c"), span, frequency, len(selected))
            note = f"LineCode.{code_name}: явная матрица умножена на длину {span:g} {code['units']}"
            if code.get("c_is_default"):
                note += (
                    ". cmatrix в файле нет: оставлен встроенный шунт OpenDSS "
                    "C1=3.4 нФ и C0=1.6 нФ на единицу длины (Cс=2.8, Cв=-0.6)"
                )
            return matrix_r, matrix_x, matrix_b, note
        if code["kind"] == "sequence":
            if interpretation != "sequence_to_phase":
                raise ValueError(f"{key}: заданы r0/x0, нужен impedance_interpretation: sequence_to_phase")
            if len(selected) != 3:
                raise ValueError(f"{key}: перевод Z0/Z1 поддержан для трёх фаз")
            self_z, mutual_z = _sequence_to_phase(complex(code["r1"], code["x1"]), complex(code["r0"], code["x0"]))
            size = 3
            matrix_r = [[(self_z.real if i == j else mutual_z.real) * span for j in range(size)] for i in range(size)]
            matrix_x = [[(self_z.imag if i == j else mutual_z.imag) * span for j in range(size)] for i in range(size)]
            matrix_b = [[0.0 for _ in range(size)] for _ in range(size)]
            return (
                matrix_r,
                matrix_x,
                matrix_b,
                f"LineCode.{code_name}: Zс=(2Z1+Z0)/3, Zв=(Z0-Z1)/3, длина {span:g} {code['units']}",
            )
        if len(selected) != 3:
            raise ValueError(f"{key}: linecode r1/x1 без r0 поддержан для трёх фаз")
        r1 = float(code["r1"]) * span
        x1 = float(code["x1"]) * span
        note = (
            f"LineCode.{code_name}: r1/x1 на {code['units']} умножены на длину {span:g}; "
            "взаимные нули — явная аппроксимация прямой последовательности, r0 в коде не было"
        )
    else:
        if not per_length and interpretation != "diagonal_from_positive_sequence":
            raise ValueError(f"{key}: r1/x1 требуют diagonal_from_positive_sequence")
        factor = length if per_length else 1.0
        r1 = float(args.get("r1", "nan")) * factor
        x1 = float(args.get("x1", "nan")) * factor
        if ("r0" in args or "x0" in args) and len(selected) == 3 and math.isfinite(r1) and math.isfinite(x1):
            r0 = float(args.get("r0", "nan")) * factor
            x0 = float(args.get("x0", "0")) * factor
            self_z, mutual_z = _sequence_to_phase(complex(r1, x1), complex(r0, x0))
            size = 3
            matrix_r = [[(self_z.real if i == j else mutual_z.real) for j in range(size)] for i in range(size)]
            matrix_x = [[(self_z.imag if i == j else mutual_z.imag) for j in range(size)] for i in range(size)]
            c1 = float(args.get("c1", "0")) * factor
            c0 = float(args.get("c0", "0")) * factor
            c_self, c_mutual = _sequence_to_phase(complex(0.0, c1), complex(0.0, c0))
            omega = 2.0 * math.pi * frequency
            matrix_b = [
                [((c_self.imag if i == j else c_mutual.imag) * 1e-9 * omega) for j in range(size)]
                for i in range(size)
            ]
            scaled = "умножены на length, как у линии OpenDSS" if per_length else "взяты как полные омы"
            return matrix_r, matrix_x, matrix_b, f"r1/x1/r0/x0 {scaled}"
        note = (
            "диагональ из r1/x1 умножена на length, как у линии OpenDSS"
            if per_length
            else "диагональ из r1/x1; длина не умножается, значения взяты как полные омы"
        )
    if not math.isfinite(r1) or not math.isfinite(x1):
        raise ValueError(f"{key}: нужны r1 и x1, linecode или rmatrix")
    size = len(selected)
    matrix_r = [[r1 if i == j else 0.0 for j in range(size)] for i in range(size)]
    matrix_x = [[x1 if i == j else 0.0 for j in range(size)] for i in range(size)]
    matrix_b = [[0.0 for _ in range(size)] for _ in range(size)]
    return matrix_r, matrix_x, matrix_b, note


def _triangle(raw: str, key: str) -> list[list[float]] | None:
    match = re.search(rf"(?i)\b{key}\s*=\s*[\[(]([^\]\)]+)[\]\)]", raw)
    if not match:
        return None
    rows = []
    for part in match.group(1).split("|"):
        values = [float(item) for item in part.split() if item]
        if values:
            rows.append(values)
    size = len(rows)
    matrix = [[0.0 for _ in range(size)] for _ in range(size)]
    full = all(len(row) == size for row in rows)
    lower = all(len(row) == index + 1 for index, row in enumerate(rows))
    if not full and not lower:
        raise ValueError(f"{key}: ожидается нижний треугольник или полная симметричная матрица")
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            matrix[index][column] = value
            if lower:
                matrix[column][index] = value
    return matrix


def _sequence_to_phase(positive: complex, zero: complex) -> tuple[complex, complex]:
    return (2.0 * positive + zero) / 3.0, (zero - positive) / 3.0


def _opendss_default_capacitance_nf(phases: int) -> list[list[float]]:
    """Шунт, который OpenDSS оставляет, если заданы только rmatrix/xmatrix.

    Конструктор LineCode держит C1=3.4 нФ и C0=1.6 нФ на единицу длины.
    Запись rmatrix заменяет Z и не трогает Yc, уже собранную как
    Cс=(2·C1+C0)/3 и Cв=(C0−C1)/3.
    """
    self_nf = (2.0 * 3.4 + 1.6) / 3.0
    mutual_nf = (1.6 - 3.4) / 3.0
    return [[self_nf if i == j else mutual_nf for j in range(phases)] for i in range(phases)]


def _linecode_susceptance(
    capacitance: list[list[float]] | None,
    span: float,
    frequency: float,
    size: int,
) -> list[list[float]]:
    if capacitance is None:
        return [[0.0 for _ in range(size)] for _ in range(size)]
    omega = 2.0 * math.pi * frequency
    return _scale_matrix(capacitance, span * 1e-9 * omega)


def _scale_matrix(matrix: list[list[float]], factor: float) -> list[list[float]]:
    return [[value * factor for value in row] for row in matrix]


def _linecodes(commands: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
    found: dict[str, dict[str, float | str]] = {}
    for command in commands:
        if command["kind"] != "new":
            continue
        klass, _, name = command["name"].partition(".")
        if klass.lower() != "linecode":
            continue
        args = command["args"]
        unit = args.get("units", "km")
        if unit not in _LENGTH_TO_M:
            raise ValueError(f"LineCode.{name}: единица {unit} не поддержана")
        phases = int(float(args.get("nphases", "3")))
        if phases not in {1, 2, 3}:
            raise ValueError(f"LineCode.{name}: поддержаны 1, 2 или 3 фазы, получено {phases}")
        raw = command.get("raw") or ""
        r_matrix = _triangle(raw, "rmatrix")
        x_matrix = _triangle(raw, "xmatrix")
        if r_matrix is not None or x_matrix is not None:
            if r_matrix is None or x_matrix is None:
                raise ValueError(f"LineCode.{name}: rmatrix и xmatrix задаются вместе")
            if len(r_matrix) != phases or len(x_matrix) != phases:
                raise ValueError(f"LineCode.{name}: размер матрицы не равен nphases")
            capacitance = _triangle(raw, "cmatrix")
            c_is_default = capacitance is None
            if c_is_default:
                capacitance = _opendss_default_capacitance_nf(phases)
            found[name.lower()] = {
                "kind": "matrix",
                "r": r_matrix,
                "x": x_matrix,
                "c": capacitance,
                "c_is_default": c_is_default,
                "units": unit,
            }
            continue
        r1 = float(args.get("r1", "nan"))
        x1 = float(args.get("x1", "nan"))
        if not math.isfinite(r1) or not math.isfinite(x1):
            raise ValueError(f"LineCode.{name}: нужны r1/x1 или rmatrix/xmatrix")
        if "r0" in args or "x0" in args:
            r0 = float(args.get("r0", "nan"))
            x0 = float(args.get("x0", "0"))
            if not math.isfinite(r0) or not math.isfinite(x0):
                raise ValueError(f"LineCode.{name}: r0/x0 заданы не числом")
            found[name.lower()] = {"kind": "sequence", "r1": r1, "x1": x1, "r0": r0, "x0": x0, "units": unit}
        else:
            found[name.lower()] = {"kind": "positive", "r1": r1, "x1": x1, "units": unit}
    return found


_LENGTH_TO_M = {"m": 1.0, "km": 1000.0, "ft": 0.3048, "kft": 304.8, "mi": 1609.344}


def _length_in(length: float, unit: str, target: str) -> float:
    if unit not in _LENGTH_TO_M or target not in _LENGTH_TO_M:
        raise ValueError(f"единица длины {unit if unit not in _LENGTH_TO_M else target} не поддержана")
    return length * _LENGTH_TO_M[unit] / _LENGTH_TO_M[target]


def _command_ledger(commands: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for command in commands:
        name = command["name"]
        kind = command["kind"]
        if kind == "directive":
            rows.append({"command": name, "status": "directive"})
        elif kind == "circuit" or (kind == "edit" and name.lower() == "vsource.source") or kind == "open":
            rows.append({"command": name, "status": "transferred"})
        elif kind == "new":
            klass = name.split(".", 1)[0].lower()
            supported = {"line", "load", "transformer", "linecode", "capacitor", "generator", "storage", "pvsystem", "vsource", "regcontrol", "loadshape"}
            if klass in {"monitor", "energymeter", "fuse"}:
                status = "ignored_non_electrical"
            elif klass in supported:
                status = "transferred"
            else:
                status = "unsupported"
            rows.append({"command": name, "status": status})
        elif kind == "edit":
            props = set(command.get("args") or {})
            load_limit = name.lower().startswith("load.") and props <= {"vminpu", "vmaxpu"}
            status = "transferred" if name.lower() == "vsource.source" or load_limit else "unsupported"
            rows.append({"command": name, "status": status})
        else:
            rows.append({"command": name, "status": "unsupported"})
    return rows


def _expand_rpn(text: str) -> str:
    """Считает встроенную арифметику OpenDSS вида (8 1000 /). Матрицы со знаком | не трогает."""

    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if "|" in body or re.search(r"[A-Za-z]", body) or not any(token in {"+", "-", "*", "/"} for token in body.split()):
            return match.group(0)
        stack: list[float] = []
        for token in body.split():
            if token in {"+", "-", "*", "/"}:
                if len(stack) < 2:
                    return match.group(0)
                right = stack.pop()
                left = stack.pop()
                if token == "+":
                    stack.append(left + right)
                elif token == "-":
                    stack.append(left - right)
                elif token == "*":
                    stack.append(left * right)
                else:
                    stack.append(left / right)
            else:
                stack.append(float(token))
        if len(stack) != 1:
            return match.group(0)
        return f"{stack[0]:.12g}"

    return re.sub(r"\(([^()]*)\)", replace, text)


def _apply_bus_coords(commands: list[dict[str, Any]], nodes: dict[str, ConnectivityNode], directory: Path, report: dict[str, Any]) -> None:
    for command in commands:
        if not str(command.get("name", "")).lower().startswith("buscoords"):
            continue
        filename = (command.get("args") or {}).get("file")
        if not filename:
            continue
        path = directory / filename
        if not path.exists():
            report["warnings"] = list(report.get("warnings") or [])
            report["warnings"].append(f"BusCoords {filename} не найден")
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = [item.strip() for item in raw.split(",") if item.strip()]
            if len(parts) < 3 or parts[0] not in nodes:
                continue
            nodes[parts[0]].x = float(parts[1])
            nodes[parts[0]].y = float(parts[2])
        report["mappings"].append({"from": f"BusCoords {filename}", "to": "node.x,y"})


def _parse_commands(text: str) -> list[dict[str, Any]]:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
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
            logical.append(_expand_rpn(buffer))
        buffer = line
    if buffer:
        logical.append(_expand_rpn(buffer))
    commands = []
    for line in logical:
        parts = line.split()
        head = parts[0].lower()
        object_name = parts[1] if len(parts) > 1 else ""
        if object_name.lower().startswith("object="):
            object_name = object_name.split("=", 1)[1]
        if head == "new" and object_name.lower().startswith("circuit"):
            commands.append({"kind": "circuit", "name": object_name, "args": _args(parts[2:])})
        elif head == "new":
            commands.append({"kind": "new", "name": parts[1], "args": _args(parts[2:]), "raw": line})
        elif head == "edit":
            commands.append({"kind": "edit", "name": parts[1], "args": _args(parts[2:])})
        elif (dotted := re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z0-9_]+)\.([A-Za-z%][A-Za-z0-9_]*)=(.+)", line)):
            commands.append(
                {
                    "kind": "edit",
                    "name": f"{dotted.group(1)}.{dotted.group(2)}",
                    "args": {dotted.group(3).lower().lstrip("%"): dotted.group(4).strip()},
                }
            )
        elif head == "open":
            commands.append({"kind": "open", "name": parts[1], "args": {}})
        elif head in {"clear", "set", "calcvoltagebases", "calcv", "solve", "buscoords"}:
            args = {"file": parts[1]} if head == "buscoords" and len(parts) > 1 else {}
            commands.append({"kind": "directive", "name": line, "args": args})
        else:
            commands.append({"kind": "unsupported", "name": line, "args": {}})
    return commands


def _args(tokens: list[str] | str) -> dict[str, str]:
    text = tokens if isinstance(tokens, str) else " ".join(tokens)
    found = {}
    for match in re.finditer(r"([%A-Za-z0-9_]+)=\s*([^\s]+)", text):
        found[match.group(1).lower().lstrip("%")] = match.group(2).strip("[]")
    return found


def _windings(raw: str) -> list[dict[str, str]]:
    parts = re.split(r"(?i)\bwdg=", raw)
    return [_args("wdg=" + part) for part in parts[1:]]

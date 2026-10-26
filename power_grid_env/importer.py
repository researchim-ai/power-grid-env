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


def _from_dss(text: str, side: dict[str, Any], report: dict[str, Any]) -> Network:
    commands = _parse_commands(text)
    report["commands"] = _command_ledger(commands)
    unsupported = [row for row in report["commands"] if row["status"] == "unsupported"]
    if unsupported:
        raise ValueError(
            "не перенесены команды: " + "; ".join(row["command"] for row in unsupported)
        )
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
    if interpretation == "diagonal_from_positive_sequence":
        report["approximations"].append(
            "Последовательные r1/x1 переведены в диагональную фазную матрицу без взаимоиндукции. "
            "Z0 не измерялся и не подставлялся отдельным значением: он совпал с Z1 только из-за нулевой взаимности."
        )
        report["assumptions"].append("diagonal_from_positive_sequence")
    elif interpretation == "explicit_phase_matrix":
        report["assumptions"].append("explicit_phase_matrix")
    else:
        raise ValueError(
            "sidecar.impedance_interpretation должен быть diagonal_from_positive_sequence или explicit_phase_matrix"
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
    if not math.isfinite(r_ohm) or not math.isfinite(x_ohm) or (r_ohm <= 0 and x_ohm <= 0):
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
            spec = transformer_side.get(f"Transformer.{name}") or {}
            vector_group = spec.get("vector_group")
            if not vector_group:
                raise ValueError(f"Transformer.{name}: в sidecar нет vector_group и заземлений")
            parsed_windings = _windings(command.get("raw") or "")
            grounds = spec.get("grounding") or []
            if len(parsed_windings) not in (2, 3) or len(grounds) != len(parsed_windings):
                raise ValueError(f"Transformer.{name}: нужны две или три обмотки и столько же явных заземлений")
            xhl = float(args.get("xhl", "nan"))
            load_loss = float(args.get("loadloss", "nan"))
            if not math.isfinite(xhl) or not math.isfinite(load_loss):
                raise ValueError(f"Transformer.{name}: нужны xhl и %loadloss")
            xht = float(args["xht"]) if "xht" in args else None
            xlt = float(args["xlt"]) if "xlt" in args else None
            if len(parsed_windings) == 3 and (xht is None or xlt is None or not math.isfinite(xht) or not math.isfinite(xlt)):
                raise ValueError(f"Transformer.{name}: для трёх обмоток нужны xht и xlt")
            built: list[Winding] = []
            for index, winding_args in enumerate(parsed_windings):
                bus = ensure_node(winding_args["bus"], float(winding_args["kv"]))
                connection_name = winding_args.get("conn", "").lower()
                if connection_name in {"wye", "y", "ln"}:
                    connection = "wye"
                elif connection_name in {"delta", "d", "ll"}:
                    connection = "delta"
                else:
                    raise ValueError(f"Transformer.{name}: соединение обмотки {index} не задано")
                ground_kind = grounds[index]
                if connection == "delta" and ground_kind != "not_applicable":
                    raise ValueError(f"Transformer.{name}: у треугольника заземление должно быть not_applicable")
                if connection == "wye" and ground_kind not in {"solid", "ungrounded", "impedance"}:
                    raise ValueError(f"Transformer.{name}: заземление звезды не задано")
                terminal_id = f"{name}:w{index}:{bus}"
                terminals.append(
                    Terminal(id=terminal_id, equipment_id=name, connectivity_node_id=bus, phases=["A", "B", "C"])
                )
                built.append(
                    Winding(
                        terminal_id=terminal_id,
                        connection=connection,  # type: ignore[arg-type]
                        grounding=GroundingSpec(kind=ground_kind),
                        nominal_ll_v=float(winding_args["kv"]) * 1000.0,
                        rated_va=float(winding_args["kva"]) * 1000.0,
                    )
                )
            transformers.append(
                Transformer(
                    id=name,
                    windings=built,
                    vector_group=vector_group,
                    x_hl_pct=xhl,
                    x_ht_pct=xht,
                    x_lt_pct=xlt,
                    load_loss_pct=load_loss,
                    no_load_loss_pct=0.0,
                    imag_pct=0.0,
                    provenance=Provenance(
                        source=f"Transformer.{name}",
                        quality="derived",
                        note="намагничивание явно обнулено; группа взята из sidecar",
                    ),
                )
            )
            report["mappings"].append({"from": f"Transformer.{name}", "to": f"transformer:{name}", "vector_group": vector_group})
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
            raw = command.get("raw") or ""
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
            attach_profile(name, args, shapes, report)
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


def _line_phases(args: dict[str, str], count: int, name: str) -> list[str]:
    if count == 3:
        return ["A", "B", "C"]
    if count == 4:
        return ["A", "B", "C", "N"]
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
    if interpretation != "diagonal_from_positive_sequence":
        raise ValueError(f"{key}: r1/x1 требуют diagonal_from_positive_sequence")
    code_name = args.get("linecode")
    if code_name and ("r1" in args or "x1" in args):
        raise ValueError(f"{key}: заданы и linecode, и r1/x1; оставьте один источник импеданса")
    if code_name:
        code = linecodes.get(code_name.lower())
        if code is None:
            raise ValueError(f"{key}: LineCode.{code_name} не объявлен")
        if len(selected) != 3:
            raise ValueError(f"{key}: linecode r1/x1 поддержан для трёх фаз")
        span = _length_in(length, unit, str(code["units"]))
        r1 = float(code["r1"]) * span
        x1 = float(code["x1"]) * span
        note = (
            f"LineCode.{code_name}: r1/x1 на {code['units']} умножены на длину {span:g}; "
            "взаимные нули — явная аппроксимация прямой последовательности"
        )
    else:
        r1 = float(args.get("r1", "nan"))
        x1 = float(args.get("x1", "nan"))
        note = "диагональ из r1/x1; длина не умножается, значения взяты как полные омы"
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
    for index, row in enumerate(rows):
        if len(row) != index + 1:
            raise ValueError(f"{key}: ожидается нижний треугольник")
        for column, value in enumerate(row):
            matrix[index][column] = value
            matrix[column][index] = value
    return matrix


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
        if unit not in {"km", "m"}:
            raise ValueError(f"LineCode.{name}: единица {unit} не поддержана, нужны km или m")
        phases = int(float(args.get("nphases", "3")))
        if phases != 3:
            raise ValueError(f"LineCode.{name}: поддержаны только три фазы, получено {phases}")
        r1 = float(args.get("r1", "nan"))
        x1 = float(args.get("x1", "nan"))
        if not math.isfinite(r1) or not math.isfinite(x1):
            raise ValueError(f"LineCode.{name}: нужны r1 и x1")
        found[name.lower()] = {"r1": r1, "x1": x1, "units": unit}
    return found


def _length_in(length: float, unit: str, target: str) -> float:
    if unit == "km":
        meters = length * 1000.0
    elif unit == "m":
        meters = length
    else:
        raise ValueError(f"единица длины {unit} не поддержана")
    if target == "km":
        return meters / 1000.0
    if target == "m":
        return meters
    raise ValueError(f"единица длины {target} не поддержана")


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
            rows.append({"command": name, "status": "transferred" if klass in supported else "unsupported"})
        elif kind == "edit":
            rows.append({"command": name, "status": "unsupported"})
        else:
            rows.append({"command": name, "status": "unsupported"})
    return rows


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
            commands.append({"kind": "new", "name": parts[1], "args": _args(parts[2:]), "raw": line})
        elif head == "edit":
            commands.append({"kind": "edit", "name": parts[1], "args": _args(parts[2:])})
        elif head == "open":
            commands.append({"kind": "open", "name": parts[1], "args": {}})
        elif head in {"clear", "set", "calcvoltagebases", "solve"}:
            commands.append({"kind": "directive", "name": line, "args": {}})
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

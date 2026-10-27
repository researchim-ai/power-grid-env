"""Дополнительное подмножество DSS: шунт, генератор, накопитель, PV, источник, профиль, РПН.

Паспортные поля без sidecar не заполняются. LoadShape не умножает паспортную
мощность: точки уходят в сценарий, интервал OpenDSS считается в часах.
"""

from __future__ import annotations

import math
import re
from typing import Any

from power_grid_env.conventions import ll_to_ln
from power_grid_env.model import (
    Generator,
    GroundingSpec,
    Inverter,
    LTCSpec,
    Provenance,
    Shunt,
    Storage,
    Terminal,
    VoltageSource,
)


def loadshapes(commands: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    found: dict[str, list[list[float]]] = {}
    for command in commands:
        if command["kind"] != "new":
            continue
        klass, _, name = command["name"].partition(".")
        if klass.lower() != "loadshape":
            continue
        raw = command.get("raw") or ""
        if "file=" in raw.lower():
            continue
        values = _number_list(raw, "mult")
        interval_h = float(command["args"].get("interval", "nan"))
        if not values or not math.isfinite(interval_h) or interval_h <= 0:
            raise ValueError(f"LoadShape.{name}: нужны mult=[...] и положительный interval в часах")
        found[name.lower()] = [[index * interval_h * 3600.0, value] for index, value in enumerate(values)]
    return found


def try_device(klass: str, name: str, args: dict[str, str], raw: str, ctx: dict[str, Any]) -> bool:
    kind = klass.lower()
    if kind == "loadshape":
        return True
    if kind == "capacitor":
        ctx["shunts"].append(_shunt(name, args, ctx))
        return True
    if kind == "generator":
        ctx["generators"].append(_generator(name, args, ctx))
        return True
    if kind == "storage":
        ctx["storages"].append(_storage(name, args, ctx))
        return True
    if kind == "pvsystem":
        ctx["inverters"].append(_pv(name, args, ctx))
        return True
    if kind == "vsource":
        ctx["sources"].append(_source(name, args, ctx))
        return True
    if kind == "regcontrol":
        _regulator(name, args, ctx)
        return True
    return False


def attach_profile(load_id: str, args: dict[str, str], shapes: dict[str, list[list[float]]], report: dict[str, Any]) -> None:
    ref = args.get("duty") or args.get("yearly") or args.get("daily")
    if not ref:
        return
    points = shapes.get(ref.lower())
    if points is None:
        report["approximations"].append(
            f"Load.{load_id}: профиль {ref} не прочитан (нет чисел или файл отсутствует). Остаётся паспортная мощность."
        )
        return
    report.setdefault("load_profiles", {})[load_id] = points
    report["approximations"].append(
        f"Load.{load_id}: LoadShape.{ref} перенесён в профиль сценария в секундах. "
        "Паспортные kW не умножены, интервал DSS взят в часах."
    )
    report["mappings"].append({"from": f"LoadShape.{ref}", "to": f"scenario.load_profile.{load_id}", "points": len(points)})


def _shunt(name: str, args: dict[str, str], ctx: dict[str, Any]) -> Shunt:
    phases = _phases(name, args, "Capacitor")
    connection = _connection(args, f"Capacitor.{name}", default=None if ctx["side"].get("schema_version") else "wye", report=ctx["report"])
    bus = ctx["ensure_node"](args["bus1"]) if len(phases) == 1 else ctx["ensure_node"](args["bus1"], float(args["kv"]))
    ground = _ground(
        ctx["grounding"],
        f"Capacitor.{name}",
        connection,
        allow_solid_default=not bool(ctx["side"].get("schema_version")),
    )
    kvar = float(args.get("kvar", "nan"))
    kv = float(args["kv"])
    if not math.isfinite(kvar) or kvar == 0:
        raise ValueError(f"Capacitor.{name}: нужен ненулевой kvar")
    if len(phases) == 1:
        voltage = kv * 1000.0
        susceptance = (kvar * 1000.0) / (voltage * voltage)
        ll = kv * 1000.0 * math.sqrt(3.0)
    else:
        voltage = ll_to_ln(kv * 1000.0)
        susceptance = (kvar * 1000.0) / (len(phases) * voltage * voltage)
        ll = kv * 1000.0
    terminal = f"{name}:{bus}"
    ctx["terminals"].append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=phases))
    ctx["report"]["mappings"].append({"from": f"Capacitor.{name}", "to": f"shunt:{name}", "kvar": kvar})
    return Shunt(
        id=name,
        terminal_id=terminal,
        phases=phases,
        connection=connection,
        b_siemens_per_phase=susceptance,
        ll_v=ll,
        provenance=Provenance(source=f"Capacitor.{name}", quality="nameplate"),
    )


def _generator(name: str, args: dict[str, str], ctx: dict[str, Any]) -> Generator:
    phases = _phases(name, args, "Generator")
    bus = ctx["ensure_node"](args["bus1"], float(args["kv"]))
    kw = float(args.get("kw", "nan"))
    if not math.isfinite(kw):
        raise ValueError(f"Generator.{name}: нет kw")
    model = args.get("model", "1")
    terminal = f"{name}:{bus}"
    ctx["terminals"].append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=phases))
    if model == "3":
        vmin = float(args.get("vminpu", "nan"))
        vmax = float(args.get("vmaxpu", "nan"))
        qmin = float(args.get("minkvar", "nan"))
        qmax = float(args.get("maxkvar", "nan"))
        if not math.isfinite(vmin) or abs(vmin - vmax) > 1e-9 or not math.isfinite(qmin) or not math.isfinite(qmax):
            raise ValueError(f"Generator.{name}: PV требует равные vminpu и vmaxpu и пределы kvar")
        mode = "pv"
        v_set = vmin
        q_var = 0.0
        q_min = qmin * 1000.0
        q_max = qmax * 1000.0
    else:
        mode = "pq"
        v_set = None
        q_var = float(args.get("kvar", "0")) * 1000.0
        q_min = None
        q_max = None
    ctx["report"]["mappings"].append({"from": f"Generator.{name}", "to": f"generator:{name}", "mode": mode, "kw": kw})
    return Generator(
        id=name,
        terminal_id=terminal,
        phases=phases,
        mode=mode,  # type: ignore[arg-type]
        p_w=kw * 1000.0,
        q_var=q_var,
        v_set_pu=v_set,
        q_min_var=q_min,
        q_max_var=q_max,
        ll_v=float(args["kv"]) * 1000.0,
        provenance=Provenance(source=f"Generator.{name}", quality="nameplate"),
    )


def _storage(name: str, args: dict[str, str], ctx: dict[str, Any]) -> Storage:
    phases = _phases(name, args, "Storage")
    connection = _connection(args, f"Storage.{name}")
    bus = ctx["ensure_node"](args["bus1"], float(args["kv"]))
    _ground(ctx["grounding"], f"Storage.{name}", connection)
    kwh = float(args.get("kwhrated", "nan"))
    kw_rated = float(args.get("kwrated", "nan"))
    stored = float(args.get("stored", "nan"))
    eta_c = float(args.get("effcharge", "nan"))
    eta_d = float(args.get("effdischarge", "nan"))
    kw = float(args.get("kw", "nan"))
    if not all(math.isfinite(item) for item in (kwh, kw_rated, stored, eta_c, eta_d, kw)):
        raise ValueError(f"Storage.{name}: нужны kwhrated, kwrated, %stored, %effcharge, %effdischarge и kw")
    terminal = f"{name}:{bus}"
    ctx["terminals"].append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=phases))
    ctx["report"]["mappings"].append({"from": f"Storage.{name}", "to": f"storage:{name}", "kw": kw, "stored_pct": stored})
    return Storage(
        id=name,
        terminal_id=terminal,
        phases=phases,
        connection=connection,
        ll_v=float(args["kv"]) * 1000.0,
        e_rated_j=kwh * 3.6e6,
        soc=stored / 100.0,
        soc_min=0.0,
        soc_max=1.0,
        p_w=kw * 1000.0,
        eta_charge=eta_c / 100.0,
        eta_discharge=eta_d / 100.0,
        s_rated_va=kw_rated * 1000.0,
        provenance=Provenance(source=f"Storage.{name}", quality="nameplate", note="положительный kw — разряд"),
    )


def _pv(name: str, args: dict[str, str], ctx: dict[str, Any]) -> Inverter:
    phases = _phases(name, args, "PVSystem")
    connection = _connection(args, f"PVSystem.{name}")
    bus = ctx["ensure_node"](args["bus1"], float(args["kv"]))
    _ground(ctx["grounding"], f"PVSystem.{name}", connection)
    kva = float(args.get("kva", "nan"))
    pmpp = float(args.get("pmpp", "nan"))
    if not math.isfinite(kva) or not math.isfinite(pmpp):
        raise ValueError(f"PVSystem.{name}: нужны kva и pmpp")
    terminal = f"{name}:{bus}"
    ctx["terminals"].append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=phases))
    ctx["report"]["approximations"].append(f"PVSystem.{name}: стационарная мощность pmpp, без суточного irradiance")
    ctx["report"]["mappings"].append({"from": f"PVSystem.{name}", "to": f"inverter:{name}", "pmpp": pmpp})
    return Inverter(
        id=name,
        terminal_id=terminal,
        phases=phases,
        connection=connection,
        ll_v=float(args["kv"]) * 1000.0,
        s_rated_va=kva * 1000.0,
        p_w=pmpp * 1000.0,
        provenance=Provenance(source=f"PVSystem.{name}", quality="nameplate"),
    )


def _source(name: str, args: dict[str, str], ctx: dict[str, Any]) -> VoltageSource:
    spec = (ctx["side"].get("sources") or {}).get(f"Vsource.{name}") or {}
    if "slack" not in spec:
        raise ValueError(f"Vsource.{name}: в sidecar нужно slack: true или false")
    kv = float(args.get("basekv", "nan"))
    r_ohm = float(args.get("r1", "nan"))
    x_ohm = float(args.get("x1", "nan"))
    if not all(math.isfinite(item) for item in (kv, r_ohm, x_ohm)) or (r_ohm <= 0 and x_ohm <= 0):
        raise ValueError(f"Vsource.{name}: нужны basekv и ненулевые r1/x1")
    bus = ctx["ensure_node"](args["bus1"], kv)
    terminal = f"{name}:{bus}"
    ctx["terminals"].append(Terminal(id=terminal, equipment_id=name, connectivity_node_id=bus, phases=["A", "B", "C"]))
    ctx["report"]["mappings"].append({"from": f"Vsource.{name}", "to": f"source:{name}", "slack": bool(spec["slack"])})
    return VoltageSource(
        id=name,
        terminal_id=terminal,
        ll_v=kv * 1000.0,
        angle_a_rad=math.radians(float(args.get("angle", "0"))),
        r_ohm=r_ohm,
        x_ohm=x_ohm,
        is_slack=bool(spec["slack"]),
        provenance=Provenance(source=f"Vsource.{name}", quality="nameplate"),
    )


def _regulator(name: str, args: dict[str, str], ctx: dict[str, Any]) -> None:
    spec = dict((ctx["side"].get("regulators") or {}).get(f"RegControl.{name}") or {})
    transformer_name = args.get("transformer")
    target = next((item for item in ctx["transformers"] if item.id == transformer_name), None)
    if target is None:
        raise ValueError(f"RegControl.{name}: трансформатор {transformer_name} не найден")
    if "v_target_pu" not in spec and "vreg" in args and not ctx["side"].get("schema_version"):
        winding_index = int(float(args.get("winding", "2"))) - 1
        kv = target.windings[winding_index].nominal_ll_v / 1000.0
        ratio = float(args.get("ptratio", "1"))
        vreg = float(args["vreg"])
        band = float(args.get("band", "2"))
        spec = {
            "winding": winding_index,
            "v_target_pu": vreg * ratio / (kv * 1000.0),
            "deadband_pu": 0.5 * band * ratio / (kv * 1000.0),
            "tap_min_pu": 0.9,
            "tap_max_pu": 1.1,
            "tap_step_pu": 0.00625,
            "pt_ratio": ratio,
            "ct_primary_a": float(args.get("ctprim", "0")) or None,
            "compensator_r_v": float(args.get("r", "0")),
            "compensator_x_v": float(args.get("x", "0")),
        }
        ctx["report"]["approximations"].append(
            f"RegControl.{name}: уставка из vreg/ptratio. Шаг отпайки 0.00625 и пределы 0.9…1.1 — стандарт OpenDSS. R/X компенсатора перенесены."
        )
    required = ("v_target_pu", "deadband_pu", "tap_min_pu", "tap_max_pu", "tap_step_pu", "winding")
    if any(key not in spec for key in required):
        raise ValueError(f"RegControl.{name}: в sidecar нет пределов РПН и номера обмотки")
    target = next((item for item in ctx["transformers"] if item.id == transformer_name), None)
    if target is None:
        raise ValueError(f"RegControl.{name}: трансформатор {transformer_name} не найден")
    target.ltc = LTCSpec(
        controlled_winding=int(spec["winding"]),
        v_target_pu=float(spec["v_target_pu"]),
        deadband_pu=float(spec["deadband_pu"]),
        tap_min_pu=float(spec["tap_min_pu"]),
        tap_max_pu=float(spec["tap_max_pu"]),
        tap_step_pu=float(spec["tap_step_pu"]),
        pt_ratio=spec.get("pt_ratio"),
        ct_primary_a=spec.get("ct_primary_a"),
        compensator_r_v=spec.get("compensator_r_v"),
        compensator_x_v=spec.get("compensator_x_v"),
    )
    ctx["report"]["mappings"].append({"from": f"RegControl.{name}", "to": f"transformer:{transformer_name}.ltc"})


def _phases(name: str, args: dict[str, str], label: str) -> list[str]:
    count = int(float(args.get("phases", "3")))
    if count == 3:
        return ["A", "B", "C"]
    if count == 1:
        node = args["bus1"].split(".")[1:]
        mapping = {"1": "A", "2": "B", "3": "C"}
        if len(node) != 1 or node[0] not in mapping:
            raise ValueError(f"{label}.{name}: однофазный объект требует bus.1, bus.2 или bus.3")
        return [mapping[node[0]]]
    raise ValueError(f"{label}.{name}: поддержаны 1 или 3 фазы")


def _connection(args: dict[str, str], key: str, *, default: str | None = None, report: dict[str, Any] | None = None) -> str:
    connection = args.get("conn", "")
    if connection in {"wye", "delta"}:
        return connection
    if default and report is not None:
        report["approximations"].append(f"{key}: conn не задан, принят {default} как в OpenDSS по умолчанию")
        return default
    raise ValueError(f"{key}: соединение conn не задано явно")


def _ground(table: dict[str, Any], key: str, connection: str, *, allow_solid_default: bool = False) -> GroundingSpec:
    raw = table.get(key)
    kind = raw.get("kind") if isinstance(raw, dict) else raw
    if connection == "delta":
        if kind not in {None, "not_applicable"}:
            raise ValueError(f"{key}: у треугольника не должно быть заземления нейтрали")
        return GroundingSpec(kind="not_applicable")
    if kind not in {"solid", "ungrounded", "impedance"}:
        if allow_solid_default:
            return GroundingSpec(kind="solid")
        raise ValueError(f"{key}: заземление звезды отсутствует в sidecar")
    return GroundingSpec(kind=kind)


def _number_list(raw: str, key: str) -> list[float] | None:
    match = re.search(rf"(?i)\b{key}\s*=\s*[\[(]([^\]\)]+)[\]\)]", raw)
    if not match:
        return None
    return [float(part) for part in match.group(1).replace(",", " ").split() if part]

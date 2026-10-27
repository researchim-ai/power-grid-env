"""SimBench CSV → каноническая сеть. Обход через DSS не используется.

Положительная последовательность линий хранится диагональной фазной матрицей:
взаимные члены нулевые, потому что Z0 в таблице нет. Это приближение, не замер.

Сверка |V| с pandapower, если пакет установлен. Порог 1e-4 о.е. объявлен
до прогона и относится только к этой паре солверов. Углы не сравниваются:
группа Dyn5 задаёт сдвиг, которого нет в допуске потокораспределения IEEE.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from benchmarks.external._common import RAW, write_json
from power_grid_env.backend_opendss import solve_network
from power_grid_env.model import (
    ConnectivityNode,
    GroundingSpec,
    Line,
    Load,
    Network,
    Provenance,
    Switch,
    Terminal,
    Transformer,
    VoltageLevel,
    VoltageSource,
    Winding,
    parse_vector_group,
)
from power_grid_env.results import SolveOptions
from power_grid_env.topology import compile_topology

DATA = RAW / "simbench" / "1-complete_data-mixed-all-0-sw"
CASES = {
    "1-LV-rural1--0-sw": "LV1.101",
    "1-MV-rural--0-sw": "MV1.101",
    "1-MV-comm--0-sw": "MV4.101",
}
# Объявлено до численного прогона. Это не допуск IEEE 1e-5: там один и тот же движок.
CROSS_SOLVER_VOLTAGE_PU = 1e-4
STUDY = "n1"
# В CSV импеданса внешней сети нет (calc_type=vavm). Для OpenDSS нужен ненулевой X.
SOURCE_X_OHM = 1e-4
_GROUP = re.compile(r"(YN|Y|D)(yn|y|zn|z|d)(\d{1,2})")


def build_case(code: str) -> Network:
    subnet = CASES[code]
    tables = _tables()
    chosen = _select(tables, subnet)
    assumptions = [
        "линии: r, x — омы прямой последовательности; взаимные члены фазной матрицы равны нулю, Z0 в CSV нет",
        f"внешняя сеть calc_type=vavm без импеданса: для OpenDSS подставлен x={SOURCE_X_OHM:.0e} Ом, это не паспорт",
        f"нагрузки — постоянная мощность снимка study case {STUDY}, без суточного профиля",
        "iMax из LineType читается как амперы",
    ]
    nodes = {row["id"]: row for row in tables["Node.csv"]}
    needed = _referenced(chosen)
    missing = needed - set(chosen["nodes"])
    for node_id in sorted(missing):
        if node_id not in nodes:
            raise ValueError(f"{code}: узел {node_id} есть в ссылке и отсутствует в Node.csv")
        chosen["nodes"][node_id] = nodes[node_id]
        assumptions.append(f"{node_id}: узел подтянут по ссылке элемента подсети {subnet}, его subnet другой")
    return _network(code, subnet, chosen, tables, assumptions)


def solve_case(code: str) -> dict:
    network = build_case(code)
    # 1e-10 на MV уводит Newton OpenDSS в расхождение за 200 итераций.
    # 1e-9 сходится, остаток KCL остаётся около 1e-8 о.е.
    result = solve_network(network, SolveOptions(tolerance=1e-9, max_iterations=80))
    topology = compile_topology(network)
    voltages = {}
    for item in result.voltages:
        if item.phase != "A" or not item.energized:
            continue
        voltages[item.bus_id] = math.hypot(item.v_real_v, item.v_imag_v) / item.v_base_ln_v
    node_voltage = {node_id: voltages.get(topology.node_to_bus[node_id]) for node_id in topology.node_to_bus}
    return {
        "case": code,
        "converged": result.converged,
        "status": result.status,
        "nodes": len(network.connectivity_nodes),
        "lines": len(network.lines),
        "switches": len(network.switches),
        "loads": len(network.loads),
        "transformers": len(network.transformers),
        "solver_tolerance": 1e-9,
        "assumptions": network.assumptions,
        "node_vm_pu": node_voltage,
        "warnings": result.warnings[:8],
    }


def compare_pandapower(code: str) -> dict:
    """Независимый положительно-последовательный расчёт. Нет пакета — нет совпадения."""
    try:
        import pandapower as pp
    except ImportError:
        return {"case": code, "verdict": "reference_solver_absent", "reference": "pandapower"}
    ours = solve_case(code)
    if not ours["converged"]:
        return {"case": code, "verdict": "fail", "reason": ours["status"], "reference": "pandapower"}
    net = _pandapower_net(code)
    pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, tolerance_mva=1e-9, max_iteration=50)
    worst = 0.0
    compared = 0
    for name, vm in zip(net.bus["name"], net.res_bus["vm_pu"], strict=True):
        left = ours["node_vm_pu"].get(name)
        if left is None:
            continue
        worst = max(worst, abs(left - float(vm)))
        compared += 1
    verdict = "pass" if compared and worst <= CROSS_SOLVER_VOLTAGE_PU else "fail"
    return {
        "case": code,
        "verdict": verdict,
        "reference": "pandapower",
        "compared_nodes": compared,
        "max_voltage_pu": worst,
        "gate_voltage_pu": CROSS_SOLVER_VOLTAGE_PU,
        "angles_compared": False,
        "same_engine_gate_claimed": False,
        "evidence": "independent_positive_sequence_solver",
    }


def write_report(out: Path) -> dict:
    cases = [compare_pandapower(code) for code in CASES]
    present = [item for item in cases if item["verdict"] != "reference_solver_absent"]
    if not present:
        verdict = "reference_solver_absent"
    elif all(item["verdict"] == "pass" for item in present) and len(present) == len(cases):
        verdict = "pass"
    else:
        verdict = "fail"
    payload = {
        "pack": "simbench-snapshot",
        "verdict": verdict,
        "study_case": STUDY,
        "gate_voltage_pu": CROSS_SOLVER_VOLTAGE_PU,
        "same_engine_gate_pu": 1e-5,
        "same_engine_gate_claimed": False,
        "angles_compared": False,
        "profiles": "not_downloaded",
        "note": (
            "Порог 1e-4 о.е. относится к паре OpenDSS и pandapower. "
            "Допуск 1e-5 одного движка здесь не заявляется: взаимное сопротивление линий обнулено, "
            "импеданс внешней сети в CSV отсутствует, сдвиг Dyn5 в эту сборку OpenDSS не передаётся."
        ),
        "cases": cases,
    }
    write_json(out / "results.json", payload)
    return payload


def _tables() -> dict[str, list[dict[str, str]]]:
    tables = {}
    for name in (
        "Node.csv",
        "Line.csv",
        "Load.csv",
        "Switch.csv",
        "LineType.csv",
        "ExternalNet.csv",
        "Transformer.csv",
        "TransformerType.csv",
        "StudyCases.csv",
    ):
        with (DATA / name).open(newline="", encoding="utf-8") as handle:
            tables[name] = list(csv.DictReader(handle, delimiter=";"))
    return tables


def _select(tables: dict[str, list[dict[str, str]]], subnet: str) -> dict[str, dict[str, dict[str, str]]]:
    def hit(row: dict[str, str]) -> bool:
        value = row.get("subnet", "")
        return value == subnet or value.startswith(subnet + "_")

    return {
        "nodes": {row["id"]: row for row in tables["Node.csv"] if hit(row)},
        "lines": {row["id"]: row for row in tables["Line.csv"] if hit(row)},
        "loads": {row["id"]: row for row in tables["Load.csv"] if hit(row)},
        "switches": {row["id"]: row for row in tables["Switch.csv"] if hit(row)},
        "transformers": {row["id"]: row for row in tables["Transformer.csv"] if hit(row)},
        "external": {row["id"]: row for row in tables["ExternalNet.csv"] if hit(row)},
    }


def _referenced(chosen: dict[str, dict[str, dict[str, str]]]) -> set[str]:
    names: set[str] = set()
    for row in chosen["lines"].values():
        names.update((row["nodeA"], row["nodeB"]))
    for row in chosen["switches"].values():
        names.update((row["nodeA"], row["nodeB"]))
    for row in chosen["loads"].values():
        names.add(row["node"])
    for row in chosen["transformers"].values():
        names.update((row["nodeHV"], row["nodeLV"]))
    for row in chosen["external"].values():
        names.add(row["node"])
    return names


def _network(code: str, subnet: str, chosen, tables, assumptions: list[str]) -> Network:
    study = {
        row["voltLvl"]: float(row["Slack_vm"])
        for row in tables["StudyCases.csv"]
        if row["Study Case"] == STUDY
    }
    line_types = {row["id"]: row for row in tables["LineType.csv"]}
    transformer_types = {row["id"]: row for row in tables["TransformerType.csv"]}
    levels: dict[str, VoltageLevel] = {}
    nodes = []
    for row in chosen["nodes"].values():
        kv = float(row["vmR"])
        level_id = f"vl-{kv:.8g}"
        levels.setdefault(level_id, VoltageLevel(id=level_id, nominal_ll_v=kv * 1000.0))
        nodes.append(
            ConnectivityNode(id=row["id"], voltage_level_id=level_id, phases=["A", "B", "C"])
        )
    terminals: list[Terminal] = []
    lines: list[Line] = []
    switches: list[Switch] = []
    loads: list[Load] = []
    transformers: list[Transformer] = []
    sources: list[VoltageSource] = []
    stamp = Provenance(source="simbench:" + code, quality="derived")

    def terminal(equipment: str, node_id: str, end: str) -> str:
        identifier = f"{equipment}:{end}"
        terminals.append(
            Terminal(
                id=identifier,
                equipment_id=equipment,
                connectivity_node_id=node_id,
                phases=["A", "B", "C"],
            )
        )
        return identifier

    for row in chosen["lines"].values():
        spec = line_types[row["type"]]
        length_km = float(row["length"])
        if length_km <= 0:
            raise ValueError(f"{row['id']}: длина не положительна, нулевая линия не подменяется")
        r_ohm = float(spec["r"]) * length_km
        x_ohm = float(spec["x"]) * length_km
        # b — мкСм/км прямой последовательности. На фазу, без взаимной ёмкости.
        b_phase = float(spec["b"]) * length_km * 1e-6
        diagonal_r = [[r_ohm if i == j else 0.0 for j in range(3)] for i in range(3)]
        diagonal_x = [[x_ohm if i == j else 0.0 for j in range(3)] for i in range(3)]
        diagonal_b = [[b_phase if i == j else 0.0 for j in range(3)] for i in range(3)]
        lines.append(
            Line(
                id=row["id"],
                terminal_from=terminal(row["id"], row["nodeA"], "a"),
                terminal_to=terminal(row["id"], row["nodeB"], "b"),
                phases=["A", "B", "C"],
                length_m=length_km * 1000.0,
                r_ohm=diagonal_r,
                x_ohm=diagonal_x,
                b_siemens=diagonal_b,
                rating_a=float(spec["iMax"]),
                provenance=stamp,
            )
        )
    for row in chosen["switches"].values():
        switches.append(
            Switch(
                id=row["id"],
                kind="breaker",
                terminal_from=terminal(row["id"], row["nodeA"], "a"),
                terminal_to=terminal(row["id"], row["nodeB"], "b"),
                phases=["A", "B", "C"],
                closed=row["cond"] == "1",
                rated_current_a=1000.0,
                interrupt_current_a=10000.0,
                operate_time_s=0.1,
                provenance=Provenance(
                    source="simbench:" + code,
                    quality="assumed",
                    note="ток и время в Switch.csv нет; замкнутость cond перенесена как есть",
                ),
            )
        )
    for row in chosen["loads"].values():
        p_phase = float(row["pLoad"]) * 1e6 / 3.0
        q_phase = float(row["qLoad"]) * 1e6 / 3.0
        host = chosen["nodes"][row["node"]]
        loads.append(
            Load(
                id=row["id"],
                terminal_id=terminal(row["id"], row["node"], "t"),
                connection="wye",
                grounding=GroundingSpec(kind="solid"),
                model="constant_pq",
                phases=["A", "B", "C"],
                p_w={"A": p_phase, "B": p_phase, "C": p_phase},
                q_var={"A": q_phase, "B": q_phase, "C": q_phase},
                rated_kv=float(host["vmR"]),
                vmin_pu=0.0,
                vmax_pu=1.5,
                provenance=stamp,
            )
        )
    for row in chosen["transformers"].values():
        spec = transformer_types[row["type"]]
        group, note = _vector_group(spec)
        assumptions.append(f"{row['id']}: {note}")
        parsed = parse_vector_group(group)
        vk = float(spec["vmImp"])
        sr = float(spec["sR"])
        vkr = float(spec["pCu"]) / (sr * 10.0)
        if vkr >= vk:
            raise ValueError(f"{row['id']}: vk не больше vkr, реактивность не выдумывается")
        xhl = math.sqrt(vk * vk - vkr * vkr)
        noload = float(spec["pFe"]) / (sr * 10.0)
        i0 = float(spec["iNoLoad"])
        imag = math.sqrt(max(i0 * i0 - noload * noload, 0.0))
        tap = 1.0 + (float(row["tappos"]) - float(spec["tapNeutr"])) * float(spec["dVm"]) / 100.0
        if row["autoTap"] not in {"0", "NULL", ""}:
            assumptions.append(f"{row['id']}: autoTap={row['autoTap']} не включён, ступень заморожена на tappos")
        windings = []
        for index, (node_id, kv_key) in enumerate((("nodeHV", "vmHV"), ("nodeLV", "vmLV"))):
            connection = parsed.connections[index]
            grounding = (
                GroundingSpec(kind="not_applicable")
                if connection == "delta"
                else GroundingSpec(kind="solid" if parsed.neutral_solid[index] else "ungrounded")
            )
            angle = 0.0
            if index > 0 and parsed.clock_hours[index] == 5:
                angle = -150.0
            windings.append(
                Winding(
                    terminal_id=terminal(row["id"], row[node_id], str(index)),
                    connection=connection,
                    grounding=grounding,
                    nominal_ll_v=float(spec[kv_key]) * 1000.0,
                    rated_va=sr * 1e6,
                    tap_pu=tap if index == 0 else 1.0,
                    angle_deg=angle,
                )
            )
        transformers.append(
            Transformer(
                id=row["id"],
                windings=windings,
                phases=3,
                vector_group=group,
                x_hl_pct=xhl,
                load_loss_pct=vkr,
                no_load_loss_pct=noload,
                imag_pct=imag,
                provenance=stamp,
            )
        )
    for row in chosen["external"].values():
        if row["calc_type"] != "vavm":
            raise ValueError(f"{row['id']}: calc_type {row['calc_type']} не разобран и не заменён на vavm")
        host = chosen["nodes"][row["node"]]
        setpoint = study[row["voltLvl"]]
        sources.append(
            VoltageSource(
                id=row["id"],
                terminal_id=terminal(row["id"], row["node"], "t"),
                ll_v=float(host["vmR"]) * 1000.0 * setpoint,
                r_ohm=0.0,
                x_ohm=SOURCE_X_OHM,
                is_slack=True,
                provenance=Provenance(
                    source="simbench:" + code,
                    quality="assumed",
                    note="модуль напряжения из study case, импеданс в CSV отсутствует",
                ),
            )
        )
    return Network(
        id=code,
        name=f"SimBench {code}",
        s_base_va=1e6,
        frequency_hz=50.0,
        voltage_levels=list(levels.values()),
        connectivity_nodes=nodes,
        terminals=terminals,
        lines=lines,
        switches=switches,
        transformers=transformers,
        loads=loads,
        sources=sources,
        assumptions=assumptions,
        provenance=stamp,
    )


def _vector_group(spec: dict[str, str]) -> tuple[str, str]:
    compact = spec["id"].replace(" ", "")
    match = _GROUP.search(compact)
    if match:
        head, tail, hour = match.group(1), match.group(2).lower(), str(int(match.group(3)))
        return head + tail + hour, "группа взята из имени типа"
    hour = int(round(float(spec["va0"]) / 30.0)) % 12
    if abs(float(spec["vmLV"]) - 0.4) < 0.05 and hour == 5:
        return "Dyn5", "в имени типа группы нет; для 20/0.4 кВ и va0=150 принят Dyn5, углы с этой группой не заявляются как сверенные"
    raise ValueError(f"{spec['id']}: группа соединений не выводится из имени и va0")


def _pandapower_net(code: str):
    import pandapower as pp

    subnet = CASES[code]
    tables = _tables()
    chosen = _select(tables, subnet)
    nodes = {row["id"]: row for row in tables["Node.csv"]}
    for node_id in _referenced(chosen) - set(chosen["nodes"]):
        chosen["nodes"][node_id] = nodes[node_id]
    study = {
        row["voltLvl"]: float(row["Slack_vm"])
        for row in tables["StudyCases.csv"]
        if row["Study Case"] == STUDY
    }
    line_types = {row["id"]: row for row in tables["LineType.csv"]}
    transformer_types = {row["id"]: row for row in tables["TransformerType.csv"]}
    net = pp.create_empty_network(f_hz=50.0, name=code)
    index: dict[str, int] = {}
    for row in chosen["nodes"].values():
        index[row["id"]] = pp.create_bus(net, vn_kv=float(row["vmR"]), name=row["id"])
    for row in chosen["lines"].values():
        spec = line_types[row["type"]]
        b_us = float(spec["b"])
        c_nf = b_us * 1e3 / (2.0 * math.pi * 50.0)
        pp.create_line_from_parameters(
            net,
            index[row["nodeA"]],
            index[row["nodeB"]],
            length_km=float(row["length"]),
            r_ohm_per_km=float(spec["r"]),
            x_ohm_per_km=float(spec["x"]),
            c_nf_per_km=c_nf,
            max_i_ka=float(spec["iMax"]) / 1000.0,
            name=row["id"],
        )
    for row in chosen["switches"].values():
        pp.create_switch(
            net,
            index[row["nodeA"]],
            index[row["nodeB"]],
            et="b",
            closed=row["cond"] == "1",
            name=row["id"],
        )
    for row in chosen["transformers"].values():
        spec = transformer_types[row["type"]]
        sr = float(spec["sR"])
        vk = float(spec["vmImp"])
        vkr = float(spec["pCu"]) / (sr * 10.0)
        pp.create_transformer_from_parameters(
            net,
            index[row["nodeHV"]],
            index[row["nodeLV"]],
            sn_mva=sr,
            vn_hv_kv=float(spec["vmHV"]),
            vn_lv_kv=float(spec["vmLV"]),
            vk_percent=vk,
            vkr_percent=vkr,
            pfe_kw=float(spec["pFe"]),
            i0_percent=float(spec["iNoLoad"]),
            shift_degree=float(spec["va0"]),
            tap_side="hv" if spec["tapside"] == "HV" else "lv",
            tap_neutral=float(spec["tapNeutr"]),
            tap_min=float(spec["tapMin"]),
            tap_max=float(spec["tapMax"]),
            tap_step_percent=float(spec["dVm"]),
            tap_pos=float(row["tappos"]),
            name=row["id"],
        )
    for row in chosen["loads"].values():
        pp.create_load(
            net,
            index[row["node"]],
            p_mw=float(row["pLoad"]),
            q_mvar=float(row["qLoad"]),
            name=row["id"],
        )
    for row in chosen["external"].values():
        host = chosen["nodes"][row["node"]]
        pp.create_ext_grid(net, index[row["node"]], vm_pu=study[row["voltLvl"]] * 1.0, name=row["id"])
        _ = host
    return net

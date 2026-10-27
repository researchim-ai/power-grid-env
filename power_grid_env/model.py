"""Каноническая модель сети. Паспорт, топология и уставки разделены по типам.

Неизвестное заземление и неизвестная схема соединения не получают значения
по умолчанию: такие объекты не проходят проверку модели.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PhaseName = Literal["A", "B", "C", "N"]
Quality = Literal["measured", "nameplate", "estimated", "assumed", "derived", "missing"]
SCHEMA_VERSION = "power-grid-env.network/v1"

_VECTOR = re.compile(
    r"^(?P<h>[DY])(?P<hn>N)?(?P<x>[dy])(?P<xn>n)?(?P<c1>\d{1,2})"
    r"(?:(?P<t>[dy])(?P<tn>n)?(?P<c2>\d{1,2}))?$"
)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    quality: Quality
    timestamp: str | None = None
    uncertainty: float | None = None
    note: str | None = None


class GroundingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["solid", "impedance", "ungrounded", "not_applicable"]
    r_ohm: float | None = None
    x_ohm: float | None = None


class VoltageLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    nominal_ll_v: float
    substation_id: str | None = None
    s_base_va: float | None = None


class ConnectivityNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    voltage_level_id: str
    phases: list[PhaseName]
    x: float | None = None
    y: float | None = None


class Terminal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    equipment_id: str
    connectivity_node_id: str
    phases: list[PhaseName]


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_from: str
    terminal_to: str
    phases: list[PhaseName]
    length_m: float
    r_ohm: list[list[float]]
    x_ohm: list[list[float]]
    b_siemens: list[list[float]]
    rating_a: float
    emergency_rating_a: float | None = None
    emergency_duration_s: float | None = None
    kron_reduction: Literal["forbidden", "allowed_solid_neutral"] = "forbidden"
    provenance: Provenance


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["breaker", "recloser", "disconnector", "fuse", "sectionalizer", "bus_connector"]
    terminal_from: str
    terminal_to: str
    phases: list[PhaseName]
    closed: bool
    rated_current_a: float
    interrupt_current_a: float
    operate_time_s: float
    max_operations: int | None = None
    operations_used: int = 0
    locked: bool = False
    lock_reason: str | None = None
    maintenance_zone: str | None = None
    tagged_for_maintenance: bool = False
    telemetered: bool = True
    operable: bool = True
    provenance: Provenance


class Winding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_id: str
    connection: Literal["wye", "delta", "series"]
    phase: Literal["A", "B", "C"] | None = None
    nodes: list[str] | None = None
    grounding: GroundingSpec
    nominal_ll_v: float
    rated_va: float
    tap_pu: float = 1.0
    r_pct: float | None = None
    angle_deg: float = 0.0


class LTCSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controlled_winding: int
    v_target_pu: float
    deadband_pu: float
    tap_min_pu: float
    tap_max_pu: float
    tap_step_pu: float
    pt_ratio: float | None = None
    ct_primary_a: float | None = None
    compensator_r_v: float | None = None
    compensator_x_v: float | None = None
    max_iterations: int = 8
    delay_s: float = 0.0


class Transformer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    windings: list[Winding]
    phases: int = 3
    vector_group: str
    x_hl_pct: float
    x_ht_pct: float | None = None
    x_lt_pct: float | None = None
    load_loss_pct: float
    no_load_loss_pct: float | None = None
    imag_pct: float | None = None
    ppm_antifloat: float = Field(default=1.0, ge=0.0)
    ltc: LTCSpec | None = None
    provenance: Provenance


class Load(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    connection: Literal["wye", "delta"]
    grounding: GroundingSpec
    model: Literal["constant_pq", "constant_z", "constant_i", "zip"]
    phases: list[Literal["A", "B", "C"]]
    p_w: dict[str, float]
    q_var: dict[str, float]
    rated_kv: float | None = None
    opendss_model: int | None = None
    zip_z: float | None = None
    zip_i: float | None = None
    zip_p: float | None = None
    zip_zq: float | None = None
    zip_iq: float | None = None
    zip_pq: float | None = None
    vmin_pu: float = 0.0
    vmax_pu: float = 1.5
    critical: bool = False
    priority: int = 0
    cold_load_multiplier: float | None = None
    cold_load_tau_s: float | None = None
    provenance: Provenance


class VoltageSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    ll_v: float
    angle_a_rad: float = 0.0
    r_ohm: float
    x_ohm: float
    r0_ohm: float | None = None
    x0_ohm: float | None = None
    is_slack: bool = True
    participation: float | None = None
    phase_pu: dict[str, float] | None = None
    provenance: Provenance


class Generator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    phases: list[Literal["A", "B", "C"]]
    mode: Literal["pq", "pv"]
    p_w: float
    q_var: float = 0.0
    v_set_pu: float | None = None
    q_min_var: float | None = None
    q_max_var: float | None = None
    participation: float | None = None
    ll_v: float
    provenance: Provenance


class Inverter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    phases: list[Literal["A", "B", "C"]]
    connection: Literal["wye", "delta"]
    ll_v: float
    s_rated_va: float
    p_w: float
    q_var: float = 0.0
    priority: Literal["P", "Q"] = "P"
    volt_var: list[tuple[float, float]] | None = None
    volt_watt: list[tuple[float, float]] | None = None
    ramp_w_per_s: float | None = None
    fault_current_multiple: float | None = None
    provenance: Provenance


class Storage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    phases: list[Literal["A", "B", "C"]]
    connection: Literal["wye", "delta"]
    ll_v: float
    e_rated_j: float
    soc: float
    soc_min: float
    soc_max: float
    p_w: float
    q_var: float = 0.0
    eta_charge: float
    eta_discharge: float
    s_rated_va: float
    ramp_w_per_s: float | None = None
    provenance: Provenance


class Shunt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    terminal_id: str
    phases: list[Literal["A", "B", "C"]]
    connection: Literal["wye", "delta"]
    b_siemens_per_phase: float
    steps: int = 1
    steps_in: int = 1
    ll_v: float
    provenance: Provenance


class NeutralGround(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    connectivity_node_id: str
    grounding: GroundingSpec
    provenance: Provenance


class Meter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    connectivity_node_id: str
    measures_voltage: bool = True
    measures_power_equipment: list[str] = Field(default_factory=list)
    delay_s: float = 0.0
    available: bool = True


class StudyFault(BaseModel):
    """Повреждение, наложенное на расчёт, а не паспорт оборудования."""

    model_config = ConfigDict(extra="forbid")

    id: str
    bus_id: str | None = None
    line_id: str | None = None
    kind: Literal["3ph", "slg", "ll", "llg"]
    phases: list[Literal["A", "B", "C"]]
    r_ohm: float
    x_ohm: float = 0.0


class Omission(BaseModel):
    """Объект, который не вошёл в расчёт. Статус зависит от задачи."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    status: Literal["ignored_non_electrical", "unsupported", "approximate"]
    power_flow: Literal["not_in_admittance", "supported", "approximate"]
    protection: Literal["unsupported", "supported"]
    note: str


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["power-grid-env.network/v1"] = SCHEMA_VERSION
    id: str
    name: str
    s_base_va: float
    frequency_hz: float = 50.0
    voltage_levels: list[VoltageLevel]
    connectivity_nodes: list[ConnectivityNode]
    terminals: list[Terminal]
    lines: list[Line] = Field(default_factory=list)
    switches: list[Switch] = Field(default_factory=list)
    transformers: list[Transformer] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)
    sources: list[VoltageSource] = Field(default_factory=list)
    generators: list[Generator] = Field(default_factory=list)
    inverters: list[Inverter] = Field(default_factory=list)
    storages: list[Storage] = Field(default_factory=list)
    shunts: list[Shunt] = Field(default_factory=list)
    neutral_grounds: list[NeutralGround] = Field(default_factory=list)
    meters: list[Meter] = Field(default_factory=list)
    study_faults: list[StudyFault] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    provenance: Provenance

    @model_validator(mode="after")
    def _validate_network(self) -> Network:
        errors = validate_network_rules(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ParsedVectorGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str
    connections: list[Literal["wye", "delta", "series"]]
    neutral_solid: list[bool]
    clock_hours: list[int]


def parse_vector_group(text: str) -> ParsedVectorGroup:
    if text.strip() == "Ii0":
        return ParsedVectorGroup(raw="Ii0", connections=["series", "series"], neutral_solid=[False, False], clock_hours=[0, 0])
    if text.strip() == "Iii0":
        return ParsedVectorGroup(
            raw="Iii0",
            connections=["series", "series", "series"],
            neutral_solid=[False, False, False],
            clock_hours=[0, 0, 0],
        )
    match = _VECTOR.match(text.strip())
    if not match:
        raise ValueError(f"неподдерживаемая группа соединений {text!r}")
    connections: list[Literal["wye", "delta"]] = []
    solid: list[bool] = []
    clocks = [0]
    hv = match.group("h")
    connections.append("wye" if hv == "Y" else "delta")
    solid.append(match.group("hn") is not None)
    xv = match.group("x")
    connections.append("wye" if xv == "y" else "delta")
    solid.append(match.group("xn") is not None)
    clocks.append(int(match.group("c1")))
    if match.group("t"):
        tv = match.group("t")
        connections.append("wye" if tv == "y" else "delta")
        solid.append(match.group("tn") is not None)
        clocks.append(int(match.group("c2")))
    for hour in clocks:
        if hour < 0 or hour > 11:
            raise ValueError(f"часовой номер {hour} вне диапазона 0..11")
    return ParsedVectorGroup(raw=text, connections=connections, neutral_solid=solid, clock_hours=clocks)


def check_grounding(connection: str, grounding: GroundingSpec, where: str) -> list[str]:
    errors: list[str] = []
    if connection in {"delta", "series"}:
        if grounding.kind != "not_applicable":
            errors.append(f"{where}: для {connection} заземление должно быть not_applicable")
        return errors
    if grounding.kind == "not_applicable":
        errors.append(f"{where}: для звезды заземление нужно задать явно")
    elif grounding.kind == "solid":
        if grounding.r_ohm not in (None, 0.0) or grounding.x_ohm not in (None, 0.0):
            errors.append(f"{where}: твёрдое заземление не содержит импеданс")
    elif grounding.kind == "impedance":
        if grounding.r_ohm is None and grounding.x_ohm is None:
            errors.append(f"{where}: импеданс нейтрали не задан")
    elif grounding.kind == "ungrounded":
        if grounding.r_ohm is not None or grounding.x_ohm is not None:
            errors.append(f"{where}: незаземлённая нейтраль не принимает импеданс")
    return errors


def _symmetric(matrix: list[list[float]], tol: float = 1e-8) -> bool:
    size = len(matrix)
    for i in range(size):
        if len(matrix[i]) != size:
            return False
        for j in range(size):
            scale = max(1.0, abs(matrix[i][j]), abs(matrix[j][i]))
            if abs(matrix[i][j] - matrix[j][i]) > tol * scale:
                return False
    return True


def validate_network_rules(network: Network) -> list[str]:
    errors: list[str] = []
    if network.s_base_va <= 0:
        errors.append("s_base_va должна быть положительной")
    if network.frequency_hz <= 0:
        errors.append("frequency_hz должна быть положительной")

    def unique(items: list[Any], label: str) -> None:
        seen: set[str] = set()
        for item in items:
            if item.id in seen:
                errors.append(f"повторяющийся идентификатор {label}:{item.id}")
            seen.add(item.id)

    unique(network.voltage_levels, "voltage_level")
    unique(network.connectivity_nodes, "node")
    unique(network.terminals, "terminal")
    for label, items in (
        ("line", network.lines),
        ("switch", network.switches),
        ("transformer", network.transformers),
        ("load", network.loads),
        ("source", network.sources),
        ("generator", network.generators),
        ("inverter", network.inverters),
        ("storage", network.storages),
        ("shunt", network.shunts),
        ("ground", network.neutral_grounds),
        ("meter", network.meters),
    ):
        unique(items, label)

    levels = {item.id: item for item in network.voltage_levels}
    nodes = {item.id: item for item in network.connectivity_nodes}
    terminals = {item.id: item for item in network.terminals}
    for level in network.voltage_levels:
        if level.nominal_ll_v <= 0:
            errors.append(f"{level.id}: номинальное напряжение должно быть положительным")
    for node in network.connectivity_nodes:
        if node.voltage_level_id not in levels:
            errors.append(f"{node.id}: неизвестный уровень напряжения")
        if not node.phases:
            errors.append(f"{node.id}: пустой набор фаз")
        if len(set(node.phases)) != len(node.phases):
            errors.append(f"{node.id}: фазы повторяются")

    equipment_ids = {
        item.id
        for group in (
            network.lines,
            network.switches,
            network.transformers,
            network.loads,
            network.sources,
            network.generators,
            network.inverters,
            network.storages,
            network.shunts,
        )
        for item in group
    }
    for terminal in network.terminals:
        if terminal.equipment_id not in equipment_ids:
            errors.append(f"{terminal.id}: оборудование {terminal.equipment_id} отсутствует")
        if terminal.connectivity_node_id not in nodes:
            errors.append(f"{terminal.id}: узел {terminal.connectivity_node_id} отсутствует")
        else:
            node_phases = set(nodes[terminal.connectivity_node_id].phases)
            if not set(terminal.phases).issubset(node_phases):
                errors.append(f"{terminal.id}: фазы терминала шире фаз узла")

    def need_terminal(terminal_id: str, where: str, phases: list[str] | None = None) -> Terminal | None:
        terminal = terminals.get(terminal_id)
        if terminal is None:
            errors.append(f"{where}: терминал {terminal_id} отсутствует")
            return None
        if phases is not None and not set(phases).issubset(set(terminal.phases)):
            errors.append(f"{where}: фазы оборудования шире фаз терминала")
        return terminal

    for line in network.lines:
        size = len(line.phases)
        for name, matrix in ("r_ohm", line.r_ohm), ("x_ohm", line.x_ohm), ("b_siemens", line.b_siemens):
            if len(matrix) != size or not _symmetric(matrix):
                errors.append(f"{line.id}: матрица {name} должна быть симметричной {size}×{size}")
        if line.length_m < 0:
            errors.append(f"{line.id}: длина отрицательна")
        if line.rating_a <= 0:
            errors.append(f"{line.id}: длительно допустимый ток должен быть положительным")
        need_terminal(line.terminal_from, line.id, line.phases)
        need_terminal(line.terminal_to, line.id, line.phases)
        if "N" in line.phases and line.kron_reduction == "allowed_solid_neutral":
            pass
        if line.kron_reduction == "allowed_solid_neutral" and "N" not in line.phases:
            errors.append(f"{line.id}: Kron запрошен без нейтрали")

    for switch in network.switches:
        need_terminal(switch.terminal_from, switch.id, switch.phases)
        need_terminal(switch.terminal_to, switch.id, switch.phases)
        if switch.kind == "disconnector" and switch.interrupt_current_a != 0:
            errors.append(f"{switch.id}: разъединитель не коммутирует ток, interrupt_current_a = 0")
        if switch.kind in {"breaker", "recloser"} and switch.interrupt_current_a <= 0:
            errors.append(f"{switch.id}: коммутационный аппарат должен иметь ток отключения")
        if switch.operate_time_s < 0:
            errors.append(f"{switch.id}: отрицательная задержка")
        if switch.operations_used < 0:
            errors.append(f"{switch.id}: отрицательный счётчик операций")

    for transformer in network.transformers:
        try:
            parsed = parse_vector_group(transformer.vector_group)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if len(parsed.connections) != len(transformer.windings):
            errors.append(f"{transformer.id}: группа {transformer.vector_group} не совпадает с числом обмоток")
            continue
        if len(transformer.windings) not in (2, 3):
            errors.append(f"{transformer.id}: поддерживаются 2 или 3 обмотки")
        if len(transformer.windings) == 3 and (
            transformer.x_ht_pct is None or transformer.x_lt_pct is None
        ):
            errors.append(f"{transformer.id}: для трёх обмоток нужны x_ht_pct и x_lt_pct")
        if transformer.no_load_loss_pct is None and transformer.imag_pct is not None:
            errors.append(f"{transformer.id}: imag_pct задан без no_load_loss_pct")
        if transformer.ltc is not None:
            ltc = transformer.ltc
            if not 0 <= ltc.controlled_winding < len(transformer.windings):
                errors.append(f"{transformer.id}: РПН ссылается на несуществующую обмотку")
            if ltc.tap_step_pu <= 0 or ltc.deadband_pu < 0 or ltc.delay_s < 0:
                errors.append(f"{transformer.id}: некорректные параметры РПН")
            if ltc.tap_min_pu > ltc.tap_max_pu:
                errors.append(f"{transformer.id}: пределы РПН переставлены")
        for index, winding in enumerate(transformer.windings):
            need_terminal(winding.terminal_id, transformer.id)
            if winding.connection != parsed.connections[index]:
                errors.append(f"{transformer.id}: обмотка {index} не совпадает с {transformer.vector_group}")
            if winding.connection == "wye":
                expects_solid = parsed.neutral_solid[index]
                if expects_solid and winding.grounding.kind != "solid":
                    errors.append(f"{transformer.id}: {transformer.vector_group} требует твёрдое заземление обмотки {index}")
                if not expects_solid and winding.grounding.kind == "solid":
                    errors.append(f"{transformer.id}: группа без N не допускает молчаливо твёрдую нейтраль обмотки {index}")
                if not expects_solid and winding.grounding.kind not in {"ungrounded", "impedance"}:
                    errors.append(f"{transformer.id}: нейтраль обмотки {index} не определена")
            errors.extend(check_grounding(winding.connection, winding.grounding, f"{transformer.id}:w{index}"))
            if winding.nominal_ll_v <= 0 or winding.rated_va <= 0:
                errors.append(f"{transformer.id}: некорректный номинал обмотки {index}")
        clocks = parsed.clock_hours
        mixed = len(set(parsed.connections)) > 1
        for hour in clocks[1:]:
            if mixed and hour not in (1, 5, 11):
                errors.append(f"{transformer.id}: для смешанных соединений поддержаны только группы 1, 5 и 11")
            if not mixed and hour != 0:
                errors.append(f"{transformer.id}: для одинаковых соединений поддержан только сдвиг 0")

    for load in network.loads:
        need_terminal(load.terminal_id, load.id, load.phases)
        errors.extend(check_grounding(load.connection, load.grounding, load.id))
        if load.connection == "delta" and len(load.phases) not in (2, 3):
            errors.append(f"{load.id}: delta-нагрузка задаётся двумя или тремя фазами")
        for phase in load.phases:
            if phase not in load.p_w or phase not in load.q_var:
                errors.append(f"{load.id}: нет P/Q фазы {phase}")
        if load.model == "zip":
            coeffs = (load.zip_z, load.zip_i, load.zip_p, load.zip_zq, load.zip_iq, load.zip_pq)
            if any(item is None for item in coeffs):
                errors.append(f"{load.id}: ZIP требует шесть коэффициентов")
            elif abs((load.zip_z or 0) + (load.zip_i or 0) + (load.zip_p or 0) - 1) > 1e-9:
                errors.append(f"{load.id}: коэффициенты ZIP по P не дают 1")
            elif abs((load.zip_zq or 0) + (load.zip_iq or 0) + (load.zip_pq or 0) - 1) > 1e-9:
                errors.append(f"{load.id}: коэффициенты ZIP по Q не дают 1")
        if (load.cold_load_multiplier is None) ^ (load.cold_load_tau_s is None):
            errors.append(f"{load.id}: cold-load pickup требует и множитель, и tau")
        if load.cold_load_multiplier is not None and load.cold_load_multiplier < 1:
            errors.append(f"{load.id}: множитель cold-load меньше 1")
        if load.cold_load_tau_s is not None and load.cold_load_tau_s <= 0:
            errors.append(f"{load.id}: tau cold-load должен быть положительным")

    for source in network.sources:
        need_terminal(source.terminal_id, source.id)
        if source.r_ohm <= 0 and source.x_ohm <= 0:
            errors.append(f"{source.id}: Тевенин источника требует ненулевой r или x; нуль не подставляется")
        if source.ll_v <= 0:
            errors.append(f"{source.id}: напряжение источника должно быть положительным")
        if source.participation is not None and source.participation < 0:
            errors.append(f"{source.id}: участие в балансе отрицательно")

    for generator in network.generators:
        need_terminal(generator.terminal_id, generator.id, generator.phases)
        if generator.mode == "pv":
            if generator.v_set_pu is None or generator.q_min_var is None or generator.q_max_var is None:
                errors.append(f"{generator.id}: PV требует уставку напряжения и пределы Q")
            elif generator.q_min_var > generator.q_max_var:
                errors.append(f"{generator.id}: пределы Q переставлены")

    for inverter in network.inverters:
        need_terminal(inverter.terminal_id, inverter.id, inverter.phases)
        if inverter.s_rated_va <= 0:
            errors.append(f"{inverter.id}: Sном должна быть положительной")
        if inverter.fault_current_multiple is not None and inverter.fault_current_multiple <= 0:
            errors.append(f"{inverter.id}: кратность тока КЗ инвертора должна быть положительной")

    for storage in network.storages:
        need_terminal(storage.terminal_id, storage.id, storage.phases)
        if not 0 <= storage.soc_min <= storage.soc <= storage.soc_max <= 1:
            errors.append(f"{storage.id}: SoC вне объявленных пределов")
        if not 0 < storage.eta_charge <= 1 or not 0 < storage.eta_discharge <= 1:
            errors.append(f"{storage.id}: КПД должен быть в (0, 1]")
        if storage.e_rated_j <= 0 or storage.s_rated_va <= 0:
            errors.append(f"{storage.id}: энергия и мощность должны быть положительными")

    for shunt in network.shunts:
        need_terminal(shunt.terminal_id, shunt.id, shunt.phases)
        if shunt.steps < 1 or not 0 <= shunt.steps_in <= shunt.steps:
            errors.append(f"{shunt.id}: ступени шунта заданы некорректно")

    for ground in network.neutral_grounds:
        if ground.connectivity_node_id not in nodes:
            errors.append(f"{ground.id}: нет узла нейтрали")
        elif "N" not in nodes[ground.connectivity_node_id].phases:
            errors.append(f"{ground.id}: узел не содержит нейтраль")
        errors.extend(check_grounding("wye", ground.grounding, ground.id))

    for meter in network.meters:
        if meter.connectivity_node_id not in nodes:
            errors.append(f"{meter.id}: нет узла измерения")
        if meter.delay_s < 0:
            errors.append(f"{meter.id}: отрицательная задержка")

    for fault in network.study_faults:
        if fault.r_ohm < 0:
            errors.append(f"{fault.id}: отрицательное сопротивление КЗ")
        if fault.bus_id is None and fault.line_id is None:
            errors.append(f"{fault.id}: КЗ должно ссылаться на узел или линию")
        if len(fault.phases) != {"3ph": 3, "slg": 1, "ll": 2, "llg": 2}[fault.kind]:
            errors.append(f"{fault.id}: число фаз не соответствует виду КЗ")
    return errors


def canonical_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()

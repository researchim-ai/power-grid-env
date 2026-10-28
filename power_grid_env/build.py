"""Сборщик небольших канонических схем для эталонов и примеров.

Приёмочный фидер в него не зашит: внешний кейс лежит в cases/acceptance
и проходит через импорт.
"""

from __future__ import annotations

import math

import numpy as np

from power_grid_env.conventions import SOLID_NEUTRAL_R_OHM
from power_grid_env.model import (
    ConnectivityNode,
    Generator,
    GroundingSpec,
    Inverter,
    Line,
    Load,
    Meter,
    Network,
    NeutralGround,
    Provenance,
    Shunt,
    Storage,
    Switch,
    Terminal,
    Transformer,
    VoltageLevel,
    VoltageSource,
    Winding,
)
from power_grid_env.reference import balanced_line_matrix

_PHASES = ("A", "B", "C", "N")


def provenance(note: str | None = None) -> Provenance:
    return Provenance(source="authored-analytical-case", quality="nameplate", note=note)


def grounding(kind: str, r_ohm: float | None = None, x_ohm: float | None = None) -> GroundingSpec:
    return GroundingSpec(kind=kind, r_ohm=r_ohm, x_ohm=x_ohm)  # type: ignore[arg-type]


def phases_of(text: str) -> list[str]:
    unknown = [symbol for symbol in text if symbol not in _PHASES]
    if unknown:
        raise ValueError(f"неизвестные фазы {unknown}")
    return list(text)


class NetBuilder:
    def __init__(
        self,
        network_id: str,
        name: str,
        kv_ll: float,
        *,
        s_base_va: float = 1.0e6,
        frequency_hz: float = 50.0,
    ) -> None:
        self.network_id = network_id
        self.name = name
        self.s_base_va = s_base_va
        self.frequency_hz = frequency_hz
        self.levels: list[VoltageLevel] = []
        self.nodes: list[ConnectivityNode] = []
        self.terminals: list[Terminal] = []
        self.lines: list[Line] = []
        self.switches: list[Switch] = []
        self.transformers: list[Transformer] = []
        self.loads: list[Load] = []
        self.sources: list[VoltageSource] = []
        self.generators: list[Generator] = []
        self.inverters: list[Inverter] = []
        self.storages: list[Storage] = []
        self.shunts: list[Shunt] = []
        self.grounds: list[NeutralGround] = []
        self.meters: list[Meter] = []
        self.assumptions: list[str] = []
        self._level_of_kv: dict[float, str] = {}
        self.level(kv_ll)

    def level(self, kv_ll: float, level_id: str | None = None) -> str:
        volts = float(kv_ll) * 1000.0
        existing = self._level_of_kv.get(round(volts, 6))
        if existing:
            return existing
        identifier = level_id or f"vl{len(self.levels)}"
        self.levels.append(VoltageLevel(id=identifier, nominal_ll_v=volts))
        self._level_of_kv[round(volts, 6)] = identifier
        return identifier

    def node(
        self,
        node_id: str,
        kv_ll: float | None = None,
        phases: str = "ABC",
        x: float | None = None,
        y: float | None = None,
    ) -> str:
        if kv_ll is None:
            level_id = self.levels[0].id
        else:
            level_id = self.level(kv_ll)
        self.nodes.append(
            ConnectivityNode(
                id=node_id,
                voltage_level_id=level_id,
                phases=phases_of(phases),  # type: ignore[arg-type]
                x=x,
                y=y,
            )
        )
        return node_id

    def _terminal(
        self,
        equipment_id: str,
        node_id: str,
        phases: str,
        terminal_id: str | None = None,
    ) -> str:
        identifier = terminal_id or f"{equipment_id}:{node_id}"
        self.terminals.append(
            Terminal(
                id=identifier,
                equipment_id=equipment_id,
                connectivity_node_id=node_id,
                phases=phases_of(phases),  # type: ignore[arg-type]
            )
        )
        return identifier

    def source(
        self,
        node_id: str,
        r_ohm: float,
        x_ohm: float,
        *,
        source_id: str = "source",
        angle_rad: float = 0.0,
        is_slack: bool = True,
        participation: float | None = None,
        ll_v: float | None = None,
    ) -> str:
        node = next(item for item in self.nodes if item.id == node_id)
        level = next(item for item in self.levels if item.id == node.voltage_level_id)
        terminal = self._terminal(source_id, node_id, "ABC")
        self.sources.append(
            VoltageSource(
                id=source_id,
                terminal_id=terminal,
                ll_v=level.nominal_ll_v if ll_v is None else ll_v,
                angle_a_rad=angle_rad,
                r_ohm=r_ohm,
                x_ohm=x_ohm,
                is_slack=is_slack,
                participation=participation,
                provenance=provenance("балансный источник прямой последовательности"),
            )
        )
        return source_id

    def line_z1(
        self,
        from_node: str,
        to_node: str,
        z1: complex,
        *,
        z_mutual: complex = 0j,
        rating_a: float = 400.0,
        length_m: float = 1000.0,
        b_phase: float = 0.0,
        line_id: str | None = None,
        phases: str = "ABC",
        emergency_rating_a: float | None = None,
        emergency_duration_s: float | None = None,
    ) -> str:
        identifier = line_id or f"line_{len(self.lines)}"
        phase_list = phases_of(phases)
        if phase_list == ["A", "B", "C"]:
            resistance, reactance = balanced_line_matrix(z1, z_mutual, 3)
            susceptance = np.zeros((3, 3))
            if b_phase:
                susceptance = np.eye(3) * b_phase
        else:
            size = len(phase_list)
            resistance = np.eye(size) * z1.real
            reactance = np.eye(size) * z1.imag
            susceptance = np.eye(size) * b_phase
        left = self._terminal(identifier, from_node, phases)
        right = self._terminal(identifier, to_node, phases)
        self.lines.append(
            Line(
                id=identifier,
                terminal_from=left,
                terminal_to=right,
                phases=phase_list,  # type: ignore[arg-type]
                length_m=length_m,
                r_ohm=resistance.tolist(),
                x_ohm=reactance.tolist(),
                b_siemens=susceptance.tolist(),
                rating_a=rating_a,
                emergency_rating_a=emergency_rating_a,
                emergency_duration_s=emergency_duration_s,
                provenance=provenance("Z1 и взаимное сопротивление заданы аналитически"),
            )
        )
        return identifier

    def line_matrix(
        self,
        from_node: str,
        to_node: str,
        r_ohm: list[list[float]],
        x_ohm: list[list[float]],
        *,
        phases: str,
        b_siemens: list[list[float]] | None = None,
        rating_a: float = 400.0,
        length_m: float = 1000.0,
        line_id: str | None = None,
        kron_reduction: str = "forbidden",
    ) -> str:
        identifier = line_id or f"line_{len(self.lines)}"
        size = len(phases_of(phases))
        susceptance = b_siemens if b_siemens is not None else [[0.0] * size for _ in range(size)]
        left = self._terminal(identifier, from_node, phases)
        right = self._terminal(identifier, to_node, phases)
        self.lines.append(
            Line(
                id=identifier,
                terminal_from=left,
                terminal_to=right,
                phases=phases_of(phases),  # type: ignore[arg-type]
                length_m=length_m,
                r_ohm=r_ohm,
                x_ohm=x_ohm,
                b_siemens=susceptance,
                rating_a=rating_a,
                kron_reduction=kron_reduction,  # type: ignore[arg-type]
                provenance=provenance("матрица импедансов задана явно"),
            )
        )
        return identifier

    def switch(
        self,
        from_node: str,
        to_node: str,
        *,
        kind: str = "breaker",
        closed: bool = True,
        switch_id: str | None = None,
        rated_current_a: float = 400.0,
        interrupt_current_a: float | None = None,
        operate_time_s: float = 0.0,
        operable: bool = True,
        locked: bool = False,
        tagged: bool = False,
        telemetered: bool = True,
        phases: str = "ABC",
    ) -> str:
        identifier = switch_id or f"sw_{len(self.switches)}"
        if interrupt_current_a is None:
            interrupt_current_a = 0.0 if kind in {"disconnector", "bus_connector"} else 8000.0
        left = self._terminal(identifier, from_node, phases)
        right = self._terminal(identifier, to_node, phases)
        self.switches.append(
            Switch(
                id=identifier,
                kind=kind,  # type: ignore[arg-type]
                terminal_from=left,
                terminal_to=right,
                phases=phases_of(phases),  # type: ignore[arg-type]
                closed=closed,
                rated_current_a=rated_current_a,
                interrupt_current_a=interrupt_current_a,
                operate_time_s=operate_time_s,
                locked=locked,
                tagged_for_maintenance=tagged,
                telemetered=telemetered,
                operable=operable,
                provenance=provenance(),
            )
        )
        return identifier

    def load(
        self,
        node_id: str,
        p_w: float | dict[str, float],
        q_var: float | dict[str, float],
        *,
        load_id: str | None = None,
        phases: str = "ABC",
        connection: str = "wye",
        ground: str = "solid",
        model: str = "constant_pq",
        critical: bool = False,
        priority: int = 0,
        zip_coeffs: tuple[float, float, float, float, float, float] | None = None,
        cold_load: tuple[float, float] | None = None,
        vmin_pu: float = 0.0,
    ) -> str:
        identifier = load_id or f"load_{len(self.loads)}"
        phase_list = [phase for phase in phases_of(phases) if phase != "N"]
        if isinstance(p_w, dict):
            active = p_w
            reactive = q_var if isinstance(q_var, dict) else {phase: float(q_var) / len(phase_list) for phase in phase_list}
        else:
            active = {phase: float(p_w) / len(phase_list) for phase in phase_list}
            reactive = {
                phase: (float(q_var) if not isinstance(q_var, dict) else q_var[phase]) / (1 if isinstance(q_var, dict) else len(phase_list))
                for phase in phase_list
            }
            if isinstance(q_var, dict):
                reactive = q_var
        if ground == "not_applicable":
            ground_spec = grounding("not_applicable")
        elif ground == "solid":
            ground_spec = grounding("solid")
        elif ground == "ungrounded":
            ground_spec = grounding("ungrounded")
        else:
            raise ValueError(ground)
        self.loads.append(
            Load(
                id=identifier,
                terminal_id=self._terminal(identifier, node_id, "".join(phase_list)),
                connection=connection,  # type: ignore[arg-type]
                grounding=ground_spec,
                model=model,  # type: ignore[arg-type]
                phases=phase_list,  # type: ignore[arg-type]
                p_w=active,
                q_var=reactive,
                zip_z=None if zip_coeffs is None else zip_coeffs[0],
                zip_i=None if zip_coeffs is None else zip_coeffs[1],
                zip_p=None if zip_coeffs is None else zip_coeffs[2],
                zip_zq=None if zip_coeffs is None else zip_coeffs[3],
                zip_iq=None if zip_coeffs is None else zip_coeffs[4],
                zip_pq=None if zip_coeffs is None else zip_coeffs[5],
                vmin_pu=vmin_pu,
                critical=critical,
                priority=priority,
                cold_load_multiplier=None if cold_load is None else cold_load[0],
                cold_load_tau_s=None if cold_load is None else cold_load[1],
                provenance=provenance(),
            )
        )
        return identifier

    def transformer(
        self,
        hv_node: str,
        lv_node: str,
        *,
        vector_group: str,
        kva: float,
        hv_kv: float,
        lv_kv: float,
        x_pct: float,
        r_pct: float,
        transformer_id: str | None = None,
        hv_ground: str = "solid",
        lv_ground: str = "solid",
        tertiary: tuple[str, float, float, str] | None = None,
        x_ht_pct: float | None = None,
        x_lt_pct: float | None = None,
    ) -> str:
        identifier = transformer_id or f"transformer_{len(self.transformers)}"
        windings = [
            self._winding(identifier, hv_node, vector_group, 0, hv_kv, kva, hv_ground),
            self._winding(identifier, lv_node, vector_group, 1, lv_kv, kva, lv_ground),
        ]
        if tertiary is not None:
            node_id, kv, kva_t, ground_kind = tertiary
            windings.append(self._winding(identifier, node_id, vector_group, 2, kv, kva_t, ground_kind))
        self.transformers.append(
            Transformer(
                id=identifier,
                windings=windings,
                vector_group=vector_group,
                x_hl_pct=x_pct,
                x_ht_pct=x_ht_pct,
                x_lt_pct=x_lt_pct,
                load_loss_pct=r_pct,
                no_load_loss_pct=0.0,
                imag_pct=0.0,
                provenance=provenance("намагничивание явно обнулено, не подобрано"),
            )
        )
        return identifier

    def _winding(self, equipment_id: str, node_id: str, vector_group: str, index: int, kv: float, kva: float, ground_kind: str) -> Winding:
        from power_grid_env.model import parse_vector_group

        parsed = parse_vector_group(vector_group)
        connection = parsed.connections[index]
        phases = "ABC"
        if ground_kind == "solid":
            ground_spec = grounding("solid")
        elif ground_kind == "ungrounded":
            ground_spec = grounding("ungrounded")
        elif ground_kind == "not_applicable":
            ground_spec = grounding("not_applicable")
        else:
            raise ValueError(ground_kind)
        return Winding(
            terminal_id=self._terminal(
                equipment_id,
                node_id,
                phases,
                terminal_id=f"{equipment_id}:w{index}:{node_id}",
            ),
            connection=connection,
            grounding=ground_spec,
            nominal_ll_v=kv * 1000.0,
            rated_va=kva * 1000.0,
            tap_pu=1.0,
        )

    def shunt(self, node_id: str, kvar: float, *, shunt_id: str | None = None, phases: str = "ABC") -> str:
        identifier = shunt_id or f"shunt_{len(self.shunts)}"
        node = next(item for item in self.nodes if item.id == node_id)
        level = next(item for item in self.levels if item.id == node.voltage_level_id)
        v_ln = level.nominal_ll_v / math.sqrt(3.0)
        phase_list = [phase for phase in phases_of(phases) if phase != "N"]
        q_phase = kvar * 1000.0 / len(phase_list)
        b_phase = q_phase / (v_ln**2)
        self.shunts.append(
            Shunt(
                id=identifier,
                terminal_id=self._terminal(identifier, node_id, "".join(phase_list)),
                phases=phase_list,  # type: ignore[arg-type]
                connection="wye",
                b_siemens_per_phase=b_phase,
                ll_v=level.nominal_ll_v,
                provenance=provenance("шунт задан мощностью при номинальном напряжении"),
            )
        )
        return identifier

    def meter(self, node_id: str, *, meter_id: str | None = None, delay_s: float = 0.0, available: bool = True) -> str:
        identifier = meter_id or f"meter_{len(self.meters)}"
        self.meters.append(
            Meter(
                id=identifier,
                connectivity_node_id=node_id,
                delay_s=delay_s,
                available=available,
            )
        )
        return identifier

    def neutral_ground(self, node_id: str, kind: str = "solid", *, ground_id: str | None = None) -> str:
        identifier = ground_id or f"ground_{len(self.grounds)}"
        if kind == "solid":
            spec = grounding("solid")
            note = f"твёрдая нейтраль представляется reactor {SOLID_NEUTRAL_R_OHM} Ом"
        elif kind == "ungrounded":
            spec = grounding("ungrounded")
            note = "незаземлённая нейтраль остаётся без связи; ток нулевой последовательности не замыкается"
        else:
            raise ValueError(kind)
        self.grounds.append(
            NeutralGround(id=identifier, connectivity_node_id=node_id, grounding=spec, provenance=provenance(note))
        )
        return identifier

    def generator(
        self,
        node_id: str,
        p_w: float,
        *,
        mode: str = "pq",
        q_var: float = 0.0,
        v_set_pu: float | None = None,
        q_min_var: float | None = None,
        q_max_var: float | None = None,
        participation: float | None = None,
        generator_id: str = "generator",
    ) -> str:
        node = next(item for item in self.nodes if item.id == node_id)
        level = next(item for item in self.levels if item.id == node.voltage_level_id)
        self.generators.append(
            Generator(
                id=generator_id,
                terminal_id=self._terminal(generator_id, node_id, "ABC"),
                phases=["A", "B", "C"],
                mode=mode,  # type: ignore[arg-type]
                p_w=p_w,
                q_var=q_var,
                v_set_pu=v_set_pu,
                q_min_var=q_min_var,
                q_max_var=q_max_var,
                participation=participation,
                ll_v=level.nominal_ll_v,
                provenance=provenance(),
            )
        )
        return generator_id

    def inverter(
        self,
        node_id: str,
        *,
        s_rated_va: float,
        p_w: float,
        q_var: float = 0.0,
        inverter_id: str = "pv",
        priority: str = "P",
        volt_var: list[tuple[float, float]] | None = None,
        volt_watt: list[tuple[float, float]] | None = None,
        fault_current_multiple: float | None = None,
        ramp_w_per_s: float | None = None,
        forming: bool = False,
        forming_r_ohm: float | None = None,
        forming_x_ohm: float | None = None,
    ) -> str:
        node = next(item for item in self.nodes if item.id == node_id)
        level = next(item for item in self.levels if item.id == node.voltage_level_id)
        self.inverters.append(
            Inverter(
                id=inverter_id,
                terminal_id=self._terminal(inverter_id, node_id, "ABC"),
                phases=["A", "B", "C"],
                connection="wye",
                ll_v=level.nominal_ll_v,
                s_rated_va=s_rated_va,
                p_w=p_w,
                q_var=q_var,
                priority=priority,  # type: ignore[arg-type]
                volt_var=volt_var,
                volt_watt=volt_watt,
                ramp_w_per_s=ramp_w_per_s,
                fault_current_multiple=fault_current_multiple,
                forming=forming,
                forming_r_ohm=forming_r_ohm,
                forming_x_ohm=forming_x_ohm,
                provenance=provenance(
                    "grid-forming — источник напряжения за импедансом"
                    if forming
                    else "grid-following не является опорой острова"
                ),
            )
        )
        return inverter_id

    def storage(
        self,
        node_id: str,
        *,
        e_j: float,
        soc: float,
        p_w: float,
        s_rated_va: float,
        eta_charge: float = 0.9,
        eta_discharge: float = 0.9,
        storage_id: str = "storage",
        soc_min: float = 0.1,
        soc_max: float = 0.9,
    ) -> str:
        node = next(item for item in self.nodes if item.id == node_id)
        level = next(item for item in self.levels if item.id == node.voltage_level_id)
        self.storages.append(
            Storage(
                id=storage_id,
                terminal_id=self._terminal(storage_id, node_id, "ABC"),
                phases=["A", "B", "C"],
                connection="wye",
                ll_v=level.nominal_ll_v,
                e_rated_j=e_j,
                soc=soc,
                soc_min=soc_min,
                soc_max=soc_max,
                p_w=p_w,
                eta_charge=eta_charge,
                eta_discharge=eta_discharge,
                s_rated_va=s_rated_va,
                provenance=provenance(),
            )
        )
        return storage_id

    def assume(self, text: str) -> None:
        self.assumptions.append(text)

    def build(self) -> Network:
        return Network(
            id=self.network_id,
            name=self.name,
            s_base_va=self.s_base_va,
            frequency_hz=self.frequency_hz,
            voltage_levels=self.levels,
            connectivity_nodes=self.nodes,
            terminals=self.terminals,
            lines=self.lines,
            switches=self.switches,
            transformers=self.transformers,
            loads=self.loads,
            sources=self.sources,
            generators=self.generators,
            inverters=self.inverters,
            storages=self.storages,
            shunts=self.shunts,
            neutral_grounds=self.grounds,
            meters=self.meters,
            assumptions=self.assumptions,
            provenance=provenance(),
        )

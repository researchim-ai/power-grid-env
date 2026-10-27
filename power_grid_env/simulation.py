"""Квазистационарный исполнитель событий.

Порядок одного момента времени: физическое исполнение ранее принятой команды,
срабатывание защиты, расчёт, публикация измерений. Решение контроллера относится
к уже опубликованным измерениям и не видит состояние, которое появится только
после задержки аппарата. Шаг управления не равен шагу физики.
"""

from __future__ import annotations

import heapq
import inspect
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from power_grid_env.backend_opendss import solve_network
from power_grid_env.conventions import ll_to_ln
from power_grid_env.der import apparent_limit, apply_ramp, clip_storage_power, cold_load_multiplier, update_soc
from power_grid_env.metrics import phase_voltage_pu, step_reward, unserved_fraction
from power_grid_env.model import Line, Load, Network, Provenance, StudyFault, Terminal
from power_grid_env.protection import RelayState, advance_relay
from power_grid_env.results import SolveResult
from power_grid_env.topology import compile_topology

SolveFn = Callable[[Network], SolveResult]


@dataclass(order=True)
class _Event:
    time_s: float
    priority: int
    seq: int
    kind: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)


def split_line_fault(
    network: Network,
    line_id: str,
    *,
    kind: str,
    phases: list[str],
    r_ohm: float,
    x_ohm: float = 0.0,
) -> Network:
    """Делит линию пополам и ставит КЗ на среднюю точку. Исходный файл не меняется."""
    net = network.model_copy(deep=True)
    line = next(item for item in net.lines if item.id == line_id)
    terminals = {item.id: item for item in net.terminals}
    node_from = terminals[line.terminal_from].connectivity_node_id
    node_to = terminals[line.terminal_to].connectivity_node_id
    template = next(item for item in net.connectivity_nodes if item.id == node_from)
    midpoint = f"{line_id}__fault_node"
    net.connectivity_nodes.append(
        template.model_copy(update={"id": midpoint, "x": None, "y": None})
    )

    def half(matrix: list[list[float]]) -> list[list[float]]:
        return [[value / 2.0 for value in row] for row in matrix]

    def add_half(new_id: str, left: str, right: str) -> None:
        first = f"{new_id}:{left}"
        second = f"{new_id}:{right}"
        phase_list = list(line.phases)
        net.terminals.append(
            Terminal(id=first, equipment_id=new_id, connectivity_node_id=left, phases=phase_list)
        )
        net.terminals.append(
            Terminal(id=second, equipment_id=new_id, connectivity_node_id=right, phases=phase_list)
        )
        net.lines.append(
            line.model_copy(
                update={
                    "id": new_id,
                    "terminal_from": first,
                    "terminal_to": second,
                    "length_m": line.length_m / 2.0,
                    "r_ohm": half(line.r_ohm),
                    "x_ohm": half(line.x_ohm),
                    "b_siemens": half(line.b_siemens),
                    "provenance": Provenance(
                        source=f"split:{line_id}",
                        quality="derived",
                        note="половина повреждённой линии",
                    ),
                }
            )
        )

    add_half(f"{line_id}__a", node_from, midpoint)
    add_half(f"{line_id}__b", midpoint, node_to)
    net.lines = [item for item in net.lines if item.id != line_id]
    net.terminals = [item for item in net.terminals if item.equipment_id != line_id]
    net.study_faults.append(
        StudyFault(
            id=f"fault_{line_id}",
            bus_id=midpoint,
            kind=kind,  # type: ignore[arg-type]
            phases=phases,  # type: ignore[arg-type]
            r_ohm=r_ohm,
            x_ohm=x_ohm,
        )
    )
    return net


class Engine:
    def __init__(self, network: Network, scenario: Any, solve: SolveFn | None = None) -> None:
        self.base_network = network
        self.scenario = scenario
        self.solve = solve or solve_network
        self.net = network
        self.time_s = 0.0
        self.queue: list[_Event] = []
        self._seq = 0
        self.rng = random.Random(0)
        self.relays: list[RelayState] = []
        self.execution_log: list[dict[str, Any]] = []
        self.ltc_outside_since: dict[str, float | None] = {}
        self.measurements: list[dict[str, Any]] = []
        self.soc: dict[str, float] = {}
        self.base_p: dict[str, dict[str, float]] = {}
        self.base_q: dict[str, dict[str, float]] = {}
        self.cold_started: dict[str, float | None] = {}
        self.storage_p: dict[str, float] = {}
        self.last_result: SolveResult | None = None
        self.trace: list[dict[str, Any]] = []
        self.dropped_trace_steps = 0
        self.history: list[dict[str, Any]] = []
        self.what_if_used = 0
        self.overload_seconds: dict[str, float] = {}
        self._ok_since: float | None = None
        self._pending = _empty_pending()
        self._operations_since_record = 0
        self.terminated = False
        self.truncated = False

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        scenario = self.scenario
        self.rng = random.Random(scenario.seed if seed is None else seed)
        self.net = self.base_network.model_copy(deep=True)
        for switch_id in getattr(scenario, "maintenance_switch_ids", []):
            self._switch(switch_id).tagged_for_maintenance = True
        if scenario.fault_line_id:
            self.net = split_line_fault(
                self.net,
                scenario.fault_line_id,
                kind=scenario.fault_kind,
                phases=list(scenario.fault_phases),
                r_ohm=scenario.fault_r_ohm,
            )
        self.time_s = 0.0
        self.queue = []
        self._seq = 0
        self.measurements = []
        self.execution_log = []
        self.ltc_outside_since = {}
        self.trace = []
        self.dropped_trace_steps = 0
        self.history = []
        self.what_if_used = 0
        self.overload_seconds = {}
        self._ok_since = None
        self._pending = _empty_pending()
        self._operations_since_record = 0
        self.terminated = False
        self.truncated = False
        self.base_p = {load.id: dict(load.p_w) for load in self.net.loads}
        self.base_q = {load.id: dict(load.q_var) for load in self.net.loads}
        self.cold_started = {load.id: None for load in self.net.loads}
        self.soc = {storage.id: storage.soc for storage in self.net.storages}
        self.storage_p = {storage.id: storage.p_w for storage in self.net.storages}
        self.relays = [_relay_from_dict(item) for item in getattr(scenario, "protection", [])]
        self.last_result = None
        self._resolve()
        self._schedule_clocks()
        return self.observation()

    def submit(self, action: dict[str, Any], *, origin: str = "controller") -> dict[str, Any]:
        acceptance = self.validate_action(action, enforce_hidden_current=True)
        acceptance["origin"] = origin
        acceptance["time_s"] = self.time_s
        if acceptance["accepted"] and action.get("kind") == "switch":
            switch = self._switch(action["equipment_id"])
            closed = action["command"] == "close"
            if switch.closed != closed:
                self._push(self.time_s + switch.operate_time_s, 1, "switch", {"id": switch.id, "closed": closed})
            else:
                acceptance["note"] = "уже в запрошенном положении"
        elif acceptance["accepted"] and action.get("kind") == "setpoint":
            self._push(
                self.time_s,
                1,
                "setpoint",
                {
                    "equipment_id": action["equipment_id"],
                    "value": float(action["value"]),
                    "unit": action.get("unit"),
                    "acceptance": acceptance,
                },
            )
        self.history.append({"time_s": self.time_s, "action": action, "acceptance": acceptance})
        return acceptance

    def advance_to(self, target_s: float) -> None:
        while self.time_s < target_s - 1e-12:
            next_time = self.queue[0].time_s if self.queue else math.inf
            protection_in = self._protection_wait()
            ltc_in = self._ltc_wait()
            step = min(target_s - self.time_s, next_time - self.time_s, protection_in, ltc_in)
            step = max(0.0, step)
            self._accumulate(step)
            self.time_s += step
            changed = False
            while self.queue and self.queue[0].time_s <= self.time_s + 1e-9:
                event = heapq.heappop(self.queue)
                changed |= self._apply_event(event)
            if protection_in <= step + 1e-9:
                changed |= self._trip_due_relays()
            if ltc_in <= step + 1e-9:
                changed = True
            if changed or self.last_result is None:
                self._resolve()
            if step <= 1e-15 and not changed and (not self.queue or self.queue[0].time_s > self.time_s + 1e-9):
                break

    def step_control(self, action: dict[str, Any] | None, *, origin: str = "controller") -> dict[str, Any]:
        if self.terminated or self.truncated:
            return self._record(action or {"kind": "hold"}, {"accepted": False, "reason": "episode_finished"}, origin)
        proposed = action or {"kind": "hold"}
        acceptance = self.submit(proposed, origin=origin)
        target = min(self.time_s + float(self.scenario.control_interval_s), float(self.scenario.horizon_s))
        self.advance_to(target)
        if self.time_s + 1e-9 >= float(self.scenario.horizon_s):
            self.truncated = True
        record = self._record(proposed, acceptance, origin)
        if record["restorable_ok"] and self._ok_since is not None and self.time_s - self._ok_since >= float(
            getattr(self.scenario, "settle_s", 0.0)
        ):
            self.terminated = True
        return record

    def observation(self) -> dict[str, Any]:
        return build_observation(self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "time_s": self.time_s,
            "network": self.net.model_dump(mode="json"),
            "queue": [asdict(event) for event in self.queue],
            "seq": self._seq,
            "rng": _export_rng(self.rng),
            "relays": [asdict(relay) for relay in self.relays],
            "execution_log": list(self.execution_log),
            "ltc_outside_since": dict(self.ltc_outside_since),
            "measurements": list(self.measurements),
            "soc": dict(self.soc),
            "base_p": self.base_p,
            "base_q": self.base_q,
            "storage_p": dict(self.storage_p),
            "cold_started": dict(self.cold_started),
            "overload_seconds": dict(self.overload_seconds),
            "ok_since": self._ok_since,
            "pending": dict(self._pending),
            "operations_since_record": self._operations_since_record,
            "what_if_used": self.what_if_used,
            "history": list(self.history),
            "trace": list(self.trace),
            "dropped_trace_steps": self.dropped_trace_steps,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "last_result": None if self.last_result is None else self.last_result.model_dump(mode="json"),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.time_s = float(snapshot["time_s"])
        self.net = Network.model_validate(snapshot["network"])
        self.queue = [_Event(**event) for event in snapshot["queue"]]
        heapq.heapify(self.queue)
        self._seq = int(snapshot["seq"])
        self.rng.setstate(_import_rng(snapshot["rng"]))
        self.relays = [RelayState(**item) for item in snapshot["relays"]]
        self.execution_log = list(snapshot.get("execution_log") or [])
        self.ltc_outside_since = dict(snapshot.get("ltc_outside_since") or {})
        self.measurements = list(snapshot["measurements"])
        self.soc = dict(snapshot["soc"])
        self.base_p = {key: dict(value) for key, value in snapshot["base_p"].items()}
        self.base_q = {key: dict(value) for key, value in snapshot["base_q"].items()}
        self.storage_p = dict(snapshot["storage_p"])
        self.cold_started = dict(snapshot["cold_started"])
        self.overload_seconds = dict(snapshot["overload_seconds"])
        self._ok_since = snapshot["ok_since"]
        self._pending = dict(snapshot["pending"])
        self._operations_since_record = int(snapshot["operations_since_record"])
        self.what_if_used = int(snapshot["what_if_used"])
        self.history = list(snapshot["history"])
        self.trace = list(snapshot.get("trace") or [])
        self.dropped_trace_steps = int(snapshot.get("dropped_trace_steps") or 0)
        self.terminated = bool(snapshot["terminated"])
        self.truncated = bool(snapshot["truncated"])
        self.last_result = None if snapshot["last_result"] is None else SolveResult.model_validate(snapshot["last_result"])

    def what_if(self, commands: list[dict[str, Any]], model: Network | None = None) -> dict[str, Any]:
        quota = int(getattr(self.scenario, "what_if_quota", 4))
        if self.what_if_used >= quota:
            return {"accepted": False, "reason": "what_if_quota", "quota": quota}
        self.what_if_used += 1
        trial = (model if model is not None else agent_model(self.base_network, self.scenario)).model_copy(deep=True)
        visible = {item.id: item.closed for item in self.net.switches if item.telemetered}
        for switch in trial.switches:
            if switch.id in visible:
                switch.closed = visible[switch.id]
        for command in commands:
            kind = command.get("kind")
            if kind == "switch":
                match = next((item for item in trial.switches if item.id == command.get("equipment_id")), None)
                if match is None:
                    return {"accepted": False, "reason": "unknown_equipment", "remaining_quota": quota - self.what_if_used}
                match.closed = command.get("command") == "close"
            elif kind == "hypothesize_fault":
                if "r_ohm" not in command or "line_id" not in command:
                    return {"accepted": False, "reason": "hypothesis_incomplete", "remaining_quota": quota - self.what_if_used}
                if not any(item.id == command["line_id"] for item in trial.lines):
                    return {"accepted": False, "reason": "unknown_equipment", "remaining_quota": quota - self.what_if_used}
                trial = split_line_fault(
                    trial,
                    command["line_id"],
                    kind=command.get("fault_kind", "3ph"),
                    phases=list(command.get("phases") or ["A", "B", "C"]),
                    r_ohm=float(command["r_ohm"]),
                    x_ohm=float(command.get("x_ohm", 0.0)),
                )
        result = self.solve(trial)
        public_nodes = {meter.connectivity_node_id for meter in trial.meters if meter.available}
        topology = compile_topology(trial)
        voltages = []
        for voltage in result.voltages:
            bus = topology.buses.get(voltage.bus_id)
            if bus is None or not any(node in public_nodes for node in bus.node_ids):
                continue
            if voltage.phase == "N":
                continue
            voltages.append(
                {
                    "bus_id": voltage.bus_id,
                    "phase": voltage.phase,
                    "v_pu": abs(voltage.complex) / voltage.v_base_ln_v if voltage.v_base_ln_v else 0.0,
                }
            )
        return {
            "accepted": True,
            "status": result.status,
            "assumptions": ["what-if считает только переданную модель, телеизмеренные положения и явные гипотезы"],
            "voltages": voltages,
            "remaining_quota": quota - self.what_if_used,
        }

    def validate_action(self, action: dict[str, Any], *, enforce_hidden_current: bool) -> dict[str, Any]:
        kind = action.get("kind")
        if kind == "hold":
            return {"accepted": True, "reason": "hold"}
        if kind not in {"switch", "setpoint"}:
            return {"accepted": False, "reason": "invalid_action", "interlock": False}
        if kind == "setpoint":
            equipment = action.get("equipment_id")
            if not any(item.id == equipment for item in (*self.net.storages, *self.net.inverters, *self.net.generators)):
                return {"accepted": False, "reason": "unknown_equipment", "interlock": False}
            value = action.get("value")
            unit = action.get("unit")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                return {"accepted": False, "reason": "invalid_value", "interlock": False}
            if unit not in {"W", "var"}:
                return {"accepted": False, "reason": "invalid_unit", "interlock": False}
            return {
                "accepted": True,
                "reason": "setpoint",
                "requested_value": float(value),
                "requested_unit": unit,
                "executed_value": None,
            }
        switch = next((item for item in self.net.switches if item.id == action.get("equipment_id")), None)
        if switch is None:
            return {"accepted": False, "reason": "unknown_equipment", "interlock": False}
        command = action.get("command")
        if command not in {"open", "close"}:
            return {"accepted": False, "reason": "invalid_action", "interlock": False}
        if not switch.operable:
            return {"accepted": False, "reason": "not_operable", "interlock": True}
        if switch.locked:
            return {"accepted": False, "reason": "locked", "interlock": True}
        if command == "close" and switch.tagged_for_maintenance:
            return {"accepted": False, "reason": "maintenance_tag", "interlock": True}
        if switch.max_operations is not None and switch.operations_used >= switch.max_operations:
            return {"accepted": False, "reason": "operation_limit", "interlock": True}
        if command == "close" and bool(getattr(self.scenario, "radiality", True)) and self._would_loop(switch):
            return {"accepted": False, "reason": "creates_loop", "interlock": True}
        if command == "close" and self._joins_two_slacks(switch):
            return {"accepted": False, "reason": "parallel_infeed", "interlock": True}
        if command == "open" and switch.kind == "disconnector" and enforce_hidden_current:
            if self._incident_current(switch) > switch.interrupt_current_a + 1e-6:
                return {"accepted": False, "reason": "disconnector_cannot_interrupt", "interlock": True}
        return {"accepted": True, "reason": "accepted"}

    def _record(self, action: dict[str, Any], acceptance: dict[str, Any], origin: str) -> dict[str, Any]:
        dt = float(self.scenario.control_interval_s)
        previous_time = self.trace[-1]["time_s"] if self.trace else 0.0
        actual_dt = max(0.0, self.time_s - previous_time)
        pending = self._pending
        self._pending = _empty_pending()
        operations = self._operations_since_record
        self._operations_since_record = 0
        record = {
            "time_s": self.time_s,
            "dt_s": actual_dt,
            "action": action,
            "acceptance": acceptance,
            "origin": origin,
            "solve_status": None if self.last_result is None else self.last_result.status,
            "observation": self.observation(),
            "privileged": _privileged_snapshot(self),
            "violations": pending["violations"],
            "restorable_ok": self._ok_since is not None,
            "ok_since_s": self._ok_since,
            "interlock_attempts": 1 if acceptance.get("interlock") else 0,
            "rejected_actions": 0 if acceptance.get("accepted") else 1,
            "operations": operations,
            "timeouts": 1 if origin == "timeout_policy" else 0,
            "energy_not_served_wh": pending["energy_not_served_wh"],
            "critical_served_wh": pending["critical_served_wh"],
            "critical_demand_wh": pending["critical_demand_wh"],
            "voltage_violation_s": pending["voltage_violation_s"],
            "voltage_violation_pu_s": pending["voltage_violation_pu_s"],
            "overload_s": pending["overload_s"],
            "overload_pu_s": pending["overload_pu_s"],
            "service_rows": pending.get("service_rows") or [],
        }
        record["reward"] = step_reward(
            energy_not_served_wh=record["energy_not_served_wh"],
            interlock_attempts=record["interlock_attempts"],
            voltage_pu_seconds=record["voltage_violation_pu_s"],
            overload_pu_seconds=record["overload_pu_s"],
            operations=record["operations"],
        )
        self.trace.append(record)
        limit = getattr(self.scenario, "trace_limit", None)
        if limit is not None:
            extra = len(self.trace) - int(limit)
            if extra > 0:
                del self.trace[:extra]
                self.dropped_trace_steps += extra
        return record

    def _interval_metrics(self, dt_s: float) -> dict[str, Any]:
        result = self.last_result
        ens_w = 0.0
        critical_served_w = 0.0
        critical_demand_w = 0.0
        voltage_s = 0.0
        voltage_pu_s = 0.0
        overload_s = 0.0
        overload_pu_s = 0.0
        violations: list[dict[str, Any]] = []
        service_rows: list[dict[str, Any]] = []
        restorable_ok = True
        if result is None or dt_s <= 0:
            return {
                "energy_not_served_wh": 0.0,
                "critical_served_wh": 0.0,
                "critical_demand_wh": 0.0,
                "voltage_violation_s": 0.0,
                "voltage_violation_pu_s": 0.0,
                "overload_s": 0.0,
                "overload_pu_s": 0.0,
                "violations": violations,
                "service_rows": [],
                "restorable_ok": False,
            }
        topology = compile_topology(self.net)
        unrestorable = set(getattr(self.scenario, "unrestorable_load_ids", []))
        v_min = float(self.scenario.v_min_pu)
        v_max = float(self.scenario.v_max_pu)
        for load in self.net.loads:
            bus_id = topology.terminal_bus[load.terminal_id]
            phase_pu = [
                phase_voltage_pu(abs(voltage.complex), voltage.v_base_ln_v)
                for voltage in result.voltages
                if voltage.bus_id == bus_id and voltage.phase in load.phases
            ]
            # base_p — паспорт. На интервале действует уже применённый профиль в p_w.
            demand = sum(load.p_w.values())
            nameplate = sum(self.base_p[load.id].values())
            energized = bool(phase_pu) and max(phase_pu) > 0.05 and result.status == "converged"
            if not energized:
                fraction = 1.0
                violations.append(self._violation("outage", load.id, 0.0, v_min, "pu"))
            else:
                fraction = unserved_fraction(phase_pu, v_min)
                for value in phase_pu:
                    if value < v_min or value > v_max:
                        voltage_s = dt_s
                        voltage_pu_s += abs(value - (v_min if value < v_min else v_max)) * dt_s
                        violations.append(
                            self._violation("voltage", load.id, value, v_min if value < v_min else v_max, "pu")
                        )
            served = demand * (1.0 - fraction)
            ens_w += demand * fraction
            service_rows.append(
                {
                    "dt_s": dt_s,
                    "demand_w": demand,
                    "nameplate_w": nameplate,
                    "supplied_w": served,
                    "curtailed_w": 0.0,
                    "phase_pu": phase_pu,
                    "energized": energized,
                    "v_min_pu": v_min,
                    "load_id": load.id,
                }
            )
            if load.critical:
                critical_demand_w += demand
                critical_served_w += served
            if load.id not in unrestorable and (fraction > 0 or any(value > v_max for value in phase_pu)):
                restorable_ok = False
        for line in self.net.lines:
            current = _equipment_current(result, line.id)
            if current > line.rating_a:
                overload_s = dt_s
                excess = (current - line.rating_a) / line.rating_a
                overload_pu_s += excess * dt_s
                self.overload_seconds[line.id] = self.overload_seconds.get(line.id, 0.0) + dt_s
                violations.append(self._violation("overload", line.id, current, line.rating_a, "A"))
                limit = line.emergency_rating_a
                duration = line.emergency_duration_s
                if limit is not None and current > limit:
                    violations.append(self._violation("emergency_overload", line.id, current, limit, "A"))
                elif duration is not None and self.overload_seconds.get(line.id, 0.0) > duration:
                    violations.append(self._violation("overload_duration", line.id, self.overload_seconds[line.id], duration, "s"))
        if overload_s > 0:
            restorable_ok = False
        hours = dt_s / 3600.0
        return {
            "energy_not_served_wh": ens_w * hours,
            "critical_served_wh": critical_served_w * hours,
            "critical_demand_wh": critical_demand_w * hours,
            "voltage_violation_s": voltage_s,
            "voltage_violation_pu_s": voltage_pu_s,
            "overload_s": overload_s,
            "overload_pu_s": overload_pu_s,
            "violations": violations,
            "service_rows": service_rows,
            "restorable_ok": restorable_ok and result.status == "converged",
        }

    def _violation(self, kind: str, equipment_id: str, value: float, limit: float, unit: str) -> dict[str, Any]:
        previous = self.history[-1]["action"] if self.history else {"kind": "hold"}
        return {
            "kind": kind,
            "time_s": self.time_s,
            "equipment_id": equipment_id,
            "value": value,
            "limit": limit,
            "unit": unit,
            "preceding_action": previous,
            "causal_claim": False,
        }

    def _call_solve(self) -> SolveResult:
        """Один контракт для прямого вызова, обёртки и процессного backend."""
        from power_grid_env.results import SolveOptions

        delayed = any(item.ltc is not None and item.ltc.delay_s > 0 for item in self.net.transformers)
        options = SolveOptions(apply_regulators=not delayed)
        solver = self.solve
        names = inspect.signature(solver).parameters
        if "options" in names:
            return solver(self.net, options)
        return solver(self.net)

    def _persist_taps(self) -> None:
        result = self.last_result
        if result is None:
            return
        solved = result.diagnostics.get("solved_taps") or {}
        for transformer in self.net.transformers:
            positions = result.tap_positions.get(transformer.id)
            if transformer.id in solved and len(transformer.windings) >= 2:
                transformer.windings[1].tap_pu = float(solved[transformer.id])
            elif positions:
                for winding, tap in zip(transformer.windings, positions, strict=False):
                    winding.tap_pu = float(tap)

    def _ltc_wait(self) -> float:
        wait = math.inf
        for transformer in self.net.transformers:
            ltc = transformer.ltc
            if ltc is None or ltc.delay_s <= 0:
                continue
            started = self.ltc_outside_since.get(transformer.id)
            if started is None:
                continue
            wait = min(wait, max(0.0, ltc.delay_s - (self.time_s - started)))
        return wait

    def _step_delayed_ltc(self) -> bool:
        """Одна ступень после выдержки. Внутренний цикл решателя при delay_s > 0 выключен."""
        result = self.last_result
        if result is None or result.status != "converged":
            return False
        changed = False
        for transformer in self.net.transformers:
            ltc = transformer.ltc
            if ltc is None or ltc.delay_s <= 0:
                continue
            measured = self._controlled_voltage_pu(transformer)
            if measured is None:
                self.ltc_outside_since[transformer.id] = None
                continue
            if abs(measured - ltc.v_target_pu) <= ltc.deadband_pu:
                self.ltc_outside_since[transformer.id] = None
                continue
            started = self.ltc_outside_since.get(transformer.id)
            if started is None:
                self.ltc_outside_since[transformer.id] = self.time_s
                continue
            if self.time_s - started + 1e-9 < ltc.delay_s:
                continue
            winding = transformer.windings[ltc.controlled_winding]
            step = ltc.tap_step_pu if measured < ltc.v_target_pu else -ltc.tap_step_pu
            updated = min(ltc.tap_max_pu, max(ltc.tap_min_pu, winding.tap_pu + step))
            if updated != winding.tap_pu:
                winding.tap_pu = updated
                changed = True
            self.ltc_outside_since[transformer.id] = self.time_s
        return changed

    def _controlled_voltage_pu(self, transformer) -> float | None:
        ltc = transformer.ltc
        if ltc is None or self.last_result is None:
            return None
        winding = transformer.windings[ltc.controlled_winding]
        bus_id = compile_topology(self.net).terminal_bus[winding.terminal_id]
        values = [
            abs(voltage.complex) / voltage.v_base_ln_v
            for voltage in self.last_result.voltages
            if voltage.bus_id == bus_id and voltage.phase in {"A", "B", "C"} and voltage.v_base_ln_v and voltage.energized
        ]
        if not values:
            return None
        return float(sum(values) / len(values))

    def _execution_allowed(self, switch, closed: bool, *, reclose: bool) -> tuple[bool, str]:
        if not switch.operable:
            return False, "not_operable"
        if switch.locked or (reclose and switch.locked):
            return False, "locked"
        if closed and switch.tagged_for_maintenance:
            return False, "maintenance_tag"
        if switch.max_operations is not None and switch.operations_used >= switch.max_operations:
            return False, "operation_limit"
        if closed and bool(getattr(self.scenario, "radiality", True)) and self._would_loop(switch):
            return False, "creates_loop"
        if closed and self._joins_two_slacks(switch):
            return False, "parallel_infeed"
        if not closed and switch.kind == "disconnector" and self._incident_current(switch) > switch.interrupt_current_a + 1e-6:
            return False, "disconnector_cannot_interrupt"
        return True, "accepted"

    def _resolve(self) -> None:
        self._apply_load_levels()
        self._apply_storage_limits()
        self.last_result = self._call_solve()
        self._persist_taps()
        if self._step_delayed_ltc():
            self.last_result = self._call_solve()
            self._persist_taps()
        self._publish()
        if any(
            load.cold_load_multiplier and self.cold_started.get(load.id) == self.time_s
            for load in self.net.loads
        ):
            self._apply_load_levels()
            self.last_result = self._call_solve()
            self._persist_taps()

    def _apply_load_levels(self) -> None:
        profiles = getattr(self.scenario, "load_profile", {}) or {}
        for load in self.net.loads:
            multiplier, reactive = _profile_pair(profiles.get(load.id, []), self.time_s)
            if load.cold_load_multiplier and load.cold_load_tau_s and self.cold_started.get(load.id) is not None:
                elapsed = self.time_s - float(self.cold_started[load.id])
                multiplier *= cold_load_multiplier(elapsed, load.cold_load_multiplier, load.cold_load_tau_s)
                reactive *= cold_load_multiplier(elapsed, load.cold_load_multiplier, load.cold_load_tau_s)
            for phase, value in self.base_p[load.id].items():
                load.p_w[phase] = value * multiplier
            for phase, value in self.base_q[load.id].items():
                load.q_var[phase] = value * reactive

    def _apply_storage_limits(self) -> None:
        dt = float(self.scenario.control_interval_s)
        for storage in self.net.storages:
            requested = self.storage_p[storage.id]
            clipped = clip_storage_power(
                requested,
                self.soc[storage.id],
                storage.soc_min,
                storage.soc_max,
                storage.e_rated_j,
                storage.eta_charge,
                storage.eta_discharge,
                dt,
                storage.s_rated_va,
            )
            storage.p_w = clipped
            storage.soc = self.soc[storage.id]

    def _publish(self) -> None:
        if self.last_result is None:
            return
        topology = compile_topology(self.net)
        delays = getattr(self.scenario, "meter_delay_s", {}) or {}
        drop = float(getattr(self.scenario, "drop_probability", 0.0))
        for meter in self.net.meters:
            if not meter.available:
                continue
            if drop and _drop_measurement(self.scenario.seed, meter.id, self.time_s, drop):
                continue
            bus_id = topology.node_to_bus[meter.connectivity_node_id]
            readings = []
            for voltage in self.last_result.voltages:
                if voltage.bus_id == bus_id and voltage.phase != "N":
                    readings.append(
                        {
                            "phase": voltage.phase,
                            "v_pu": abs(voltage.complex) / voltage.v_base_ln_v if voltage.v_base_ln_v else 0.0,
                            "angle_rad": math.atan2(voltage.v_imag_v, voltage.v_real_v),
                        }
                    )
            delay = float(delays.get(meter.id, meter.delay_s))
            self.measurements.append(
                {
                    "meter_id": meter.id,
                    "node_id": meter.connectivity_node_id,
                    "produced_s": self.time_s,
                    "available_s": self.time_s + delay,
                    "readings": readings,
                }
            )
        self._update_cold_timers(topology)

    def _update_cold_timers(self, topology) -> None:
        if self.last_result is None or self.last_result.status != "converged":
            return
        v_min = float(self.scenario.v_min_pu)
        for load in self.net.loads:
            if not load.cold_load_multiplier:
                continue
            bus_id = topology.terminal_bus[load.terminal_id]
            phase_pu = [
                abs(voltage.complex) / voltage.v_base_ln_v
                for voltage in self.last_result.voltages
                if voltage.bus_id == bus_id and voltage.phase in load.phases and voltage.v_base_ln_v
            ]
            served = bool(phase_pu) and min(phase_pu) >= v_min
            if served and self.cold_started.get(load.id) is None:
                self.cold_started[load.id] = self.time_s
            if not served:
                self.cold_started[load.id] = None

    def _accumulate(self, dt_s: float) -> None:
        if dt_s <= 0 or self.last_result is None:
            return
        piece = self._interval_metrics(dt_s)
        for key in (
            "energy_not_served_wh",
            "critical_served_wh",
            "critical_demand_wh",
            "voltage_violation_s",
            "voltage_violation_pu_s",
            "overload_s",
            "overload_pu_s",
        ):
            self._pending[key] += piece[key]
        self._pending["violations"].extend(piece["violations"])
        self._pending["service_rows"].extend(piece.get("service_rows") or [])
        if piece["restorable_ok"]:
            if self._ok_since is None:
                self._ok_since = self.time_s
        else:
            self._ok_since = None
        for storage in self.net.storages:
            executed = -sum(
                flow.p_into_w
                for flow in self.last_result.flows
                if flow.equipment_id == storage.id and flow.phase in {"A", "B", "C"}
            )
            self.soc[storage.id] = update_soc(
                self.soc[storage.id],
                executed,
                storage.e_rated_j,
                storage.eta_charge,
                storage.eta_discharge,
                dt_s,
                storage.soc_min,
                storage.soc_max,
            )
        for relay in self.relays:
            signal = self._relay_signal(relay)
            advance_relay(relay, signal, dt_s)

    def _protection_wait(self) -> float:
        if not self.relays or self.last_result is None:
            return math.inf
        wait = math.inf
        for relay in self.relays:
            if relay.lockout:
                continue
            signal = self._relay_signal(relay)
            if relay.kind == "overcurrent" and relay.curve != "definite":
                if signal <= relay.pickup:
                    continue
                from power_grid_env.protection import iec_operate_time

                operate = iec_operate_time(signal, relay.pickup, relay.curve, relay.time_multiplier_s)
                remain = max(0.0, 1.0 - relay.accumulator_s) * operate
            else:
                active = signal > relay.pickup if relay.kind != "undervoltage" else signal < relay.pickup
                if not active:
                    continue
                remain = max(0.0, relay.time_multiplier_s + relay.breaker_delay_s - relay.accumulator_s)
            wait = min(wait, remain)
        return wait

    def _trip_due_relays(self) -> bool:
        changed = False
        for relay in self.relays:
            if not relay.tripped:
                continue
            switch = next((item for item in self.net.switches if item.id == relay.equipment_id), None)
            if switch is not None and switch.closed:
                switch.closed = False
                switch.operations_used += 1
                relay.tripped = False
                changed = True
                if relay.reclose_delay_s is not None and not relay.lockout:
                    self._push(self.time_s + relay.reclose_delay_s, 2, "switch", {"id": switch.id, "closed": True, "reclose": True})
            else:
                relay.tripped = False
        return changed

    def _relay_signal(self, relay: RelayState) -> float:
        if self.last_result is None:
            return 0.0
        if relay.kind == "overcurrent":
            measured = relay.measured_equipment_id
            if measured:
                return _equipment_current(self.last_result, measured)
            switch = next((item for item in self.net.switches if item.id == relay.equipment_id), None)
            if switch is not None:
                return self._switch_current_a(switch)
            return _equipment_current(self.last_result, relay.equipment_id)
        node_id = relay.measured_node_id
        if not node_id:
            if "measured_node_id" not in relay.notes:
                relay.notes.append("measured_node_id")
            return 1.0 if relay.kind == "undervoltage" else 0.0
        topology = compile_topology(self.net)
        bus_id = topology.node_to_bus.get(node_id)
        if bus_id is None:
            return 1.0 if relay.kind == "undervoltage" else 0.0
        voltages = [
            abs(voltage.complex) / voltage.v_base_ln_v
            for voltage in self.last_result.voltages
            if voltage.bus_id == bus_id and voltage.phase in {"A", "B", "C"} and voltage.v_base_ln_v
        ]
        if not voltages:
            return 0.0
        return min(voltages) if relay.kind == "undervoltage" else max(voltages)

    def _apply_event(self, event: _Event) -> bool:
        if event.kind in {"sample", "profile"}:
            return True
        if event.kind == "setpoint":
            return self._execute_setpoint(event.payload)
        if event.kind != "switch":
            return False
        switch = self._switch(event.payload["id"])
        closed = bool(event.payload["closed"])
        allowed, reason = self._execution_allowed(switch, closed, reclose=bool(event.payload.get("reclose")))
        self.execution_log.append(
            {
                "time_s": self.time_s,
                "equipment_id": switch.id,
                "command": "close" if closed else "open",
                "accepted": allowed,
                "reason": reason,
            }
        )
        if not allowed or switch.closed == closed:
            return False
        switch.closed = closed
        switch.operations_used += 1
        self._operations_since_record += 1
        return True

    def _apply_setpoint(self, action: dict[str, Any]) -> None:
        equipment = action["equipment_id"]
        value = float(action["value"])
        for storage in self.net.storages:
            if storage.id == equipment:
                self.storage_p[equipment] = apply_ramp(
                    self.storage_p[equipment], value, storage.ramp_w_per_s, float(self.scenario.control_interval_s)
                )
                return
        for inverter in self.net.inverters:
            if inverter.id == equipment:
                inverter.p_w = value
                return

    def _push(self, time_s: float, priority: int, kind: str, payload: dict[str, Any]) -> None:
        heapq.heappush(self.queue, _Event(time_s, priority, self._seq, kind, payload))
        self._seq += 1

    def _schedule_clocks(self) -> None:
        horizon = float(self.scenario.horizon_s)
        period = getattr(self.scenario, "measurement_period_s", None)
        if period is None:
            period = float(self.scenario.control_interval_s)
        stamp = float(period)
        while stamp <= horizon + 1e-9:
            self._push(stamp, 4, "sample", {})
            stamp += float(period)
        profiles = getattr(self.scenario, "load_profile", {}) or {}
        seen: set[float] = set()
        for points in profiles.values():
            for point in points:
                when = float(point[0])
                if when > 0 and when not in seen:
                    seen.add(when)
                    self._push(when, 0, "profile", {})

    def _execute_setpoint(self, payload: dict[str, Any]) -> bool:
        equipment = payload["equipment_id"]
        value = float(payload["value"])
        unit = payload.get("unit")
        executed: float | None = None
        for generator in self.net.generators:
            if generator.id == equipment and unit == "W":
                generator.p_w = value
                executed = value
        for inverter in self.net.inverters:
            if inverter.id == equipment and unit == "W":
                active, _reactive = apparent_limit(value, inverter.q_var, inverter.s_rated_va, inverter.priority)
                inverter.p_w = active
                executed = active
            elif inverter.id == equipment and unit == "var":
                _active, reactive = apparent_limit(inverter.p_w, value, inverter.s_rated_va, inverter.priority)
                inverter.q_var = reactive
                executed = reactive
        for storage in self.net.storages:
            if storage.id == equipment and unit == "W":
                self.storage_p[equipment] = apply_ramp(
                    self.storage_p.get(equipment, storage.p_w),
                    value,
                    storage.ramp_w_per_s,
                    float(self.scenario.control_interval_s),
                )
                executed = self.storage_p[equipment]
        acceptance = payload.get("acceptance")
        if isinstance(acceptance, dict):
            acceptance["executed_value"] = executed
            acceptance["executed_unit"] = unit if executed is not None else None
        return executed is not None

    def _switch_current_a(self, switch) -> float:
        if self.last_result is None or not switch.closed:
            return 0.0
        node_from, node_to = _switch_nodes(self.net, switch)
        downstream = _reachable_nodes(self.net, node_to, ignore_switch=switch.id)
        if node_from in downstream:
            return self._incident_current(switch)
        terminals = {item.id: item for item in self.net.terminals}
        totals = {"A": 0j, "B": 0j, "C": 0j}
        for line in self.net.lines:
            ends = (
                (0, terminals[line.terminal_from].connectivity_node_id),
                (1, terminals[line.terminal_to].connectivity_node_id),
            )
            for index, node in ends:
                other = ends[1 - index][1]
                if node != node_to or other not in downstream:
                    continue
                for flow in self.last_result.flows:
                    if flow.equipment_id == line.id and flow.terminal_index == index and flow.phase in totals:
                        totals[flow.phase] += flow.current
        if max(abs(value) for value in totals.values()) > 0:
            return max(abs(value) for value in totals.values())
        return self._incident_current(switch)

    def _switch(self, switch_id: str):
        return next(item for item in self.net.switches if item.id == switch_id)

    def _would_loop(self, switch) -> bool:
        return _nodes_connected(self.net, _switch_nodes(self.net, switch), ignore_switch=switch.id)

    def _joins_two_slacks(self, switch) -> bool:
        """Два питающих источника нельзя соединять молча. Это не кольцо линий."""
        topology = compile_topology(self.net)
        left_node, right_node = _switch_nodes(self.net, switch)
        left = topology.node_to_bus.get(left_node)
        right = topology.node_to_bus.get(right_node)
        if left is None or right is None:
            return False
        left_island = topology.bus_island[left]
        right_island = topology.bus_island[right]
        if left_island == right_island:
            return False

        def slacks(island_id: str) -> int:
            island = next(item for item in topology.islands if item.id == island_id)
            return sum(1 for source in self.net.sources if source.id in island.source_ids and source.is_slack)

        return slacks(left_island) > 0 and slacks(right_island) > 0

    def _incident_current(self, switch) -> float:
        if self.last_result is None:
            return 0.0
        nodes = set(_switch_nodes(self.net, switch))
        terminals = {item.id: item for item in self.net.terminals}
        equipment = set()
        for terminal in self.net.terminals:
            if terminal.connectivity_node_id in nodes and terminal.equipment_id != switch.id:
                equipment.add(terminal.equipment_id)
        current = 0.0
        for flow in self.last_result.flows:
            if flow.equipment_id in equipment and flow.phase in {"A", "B", "C"}:
                current = max(current, abs(flow.current))
        return current


def _export_rng(rng: random.Random) -> dict[str, Any]:
    version, sequence, gauss = rng.getstate()
    return {"version": version, "state": list(sequence), "gauss": gauss}


def _import_rng(payload: Any) -> tuple:
    if isinstance(payload, dict):
        return (payload["version"], tuple(payload["state"]), payload["gauss"])
    version, sequence, gauss = payload
    return (version, tuple(sequence), gauss)


def _drop_measurement(seed: int, meter_id: str, produced_s: float, probability: float) -> bool:
    if probability <= 0:
        return False
    if probability >= 1:
        return True
    return random.Random(f"{seed}|{meter_id}|{round(produced_s, 6)}").random() < probability


def _reachable_nodes(network: Network, start: str, ignore_switch: str | None) -> set[str]:
    terminals = {item.id: item for item in network.terminals}
    adjacent: dict[str, set[str]] = {}

    def link(left: str, right: str) -> None:
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)

    for switch in network.switches:
        if not switch.closed or switch.id == ignore_switch:
            continue
        link(*_switch_nodes(network, switch))
    for line in network.lines:
        link(terminals[line.terminal_from].connectivity_node_id, terminals[line.terminal_to].connectivity_node_id)
    for transformer in network.transformers:
        nodes = [terminals[winding.terminal_id].connectivity_node_id for winding in transformer.windings]
        for left in nodes:
            for right in nodes:
                if left != right:
                    link(left, right)
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for nxt in adjacent.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _empty_pending() -> dict[str, Any]:
    return {
        "energy_not_served_wh": 0.0,
        "critical_served_wh": 0.0,
        "critical_demand_wh": 0.0,
        "voltage_violation_s": 0.0,
        "voltage_violation_pu_s": 0.0,
        "overload_s": 0.0,
        "overload_pu_s": 0.0,
        "violations": [],
        "service_rows": [],
    }


def _privileged_snapshot(engine: Engine) -> dict[str, Any]:
    result = engine.last_result
    if result is None:
        return {"source": "privileged", "voltages": [], "currents": []}
    topology = compile_topology(engine.net)
    voltages = [
        {
            "bus_id": voltage.bus_id,
            "phase": voltage.phase,
            "v_pu": abs(voltage.complex) / voltage.v_base_ln_v if voltage.v_base_ln_v else 0.0,
            "energized": voltage.energized,
        }
        for voltage in result.voltages
        if voltage.phase in {"A", "B", "C"}
    ]
    currents = []
    for line in engine.net.lines:
        currents.append({"equipment_id": line.id, "current_a": _equipment_current(result, line.id), "rating_a": line.rating_a})
    return {
        "source": "privileged",
        "solve_status": result.status,
        "node_of_bus": {bus_id: list(bus.node_ids) for bus_id, bus in topology.buses.items()},
        "voltages": voltages,
        "currents": currents,
    }


def _relay_from_dict(raw: dict[str, Any]) -> RelayState:
    return RelayState(
        relay_id=raw["id"],
        kind=raw.get("kind", "overcurrent"),
        equipment_id=raw["equipment_id"],
        pickup=float(raw["pickup"]),
        curve=raw.get("curve", "definite"),
        time_multiplier_s=float(raw.get("time_multiplier_s", 1.0)),
        breaker_delay_s=float(raw.get("breaker_delay_s", 0.0)),
        reset_s=float(raw.get("reset_s", 1.0)),
        max_shots=raw.get("max_shots"),
        reclose_delay_s=raw.get("reclose_delay_s"),
        directional=bool(raw.get("directional", False)),
        measured_equipment_id=raw.get("measured_equipment_id"),
        measured_node_id=raw.get("measured_node_id"),
    )


def _profile_multiplier(points: list[tuple[float, float]] | list[list[float]], time_s: float) -> float:
    return _profile_pair(points, time_s)[0]


def _profile_pair(points: list[tuple[float, float]] | list[list[float]], time_s: float) -> tuple[float, float]:
    """Вторая координата — множитель P. Третья, если есть, — множитель Q, иначе он совпадает с P."""
    if not points:
        return 1.0, 1.0
    ordered = []
    for point in points:
        active = float(point[1])
        reactive = float(point[2]) if len(point) > 2 else active
        ordered.append((float(point[0]), active, reactive))
    ordered.sort()
    current = (ordered[0][1], ordered[0][2])
    for stamp, active, reactive in ordered:
        if stamp <= time_s + 1e-12:
            current = (active, reactive)
        else:
            break
    return current


def _switch_nodes(network: Network, switch) -> tuple[str, str]:
    terminals = {item.id: item for item in network.terminals}
    return (
        terminals[switch.terminal_from].connectivity_node_id,
        terminals[switch.terminal_to].connectivity_node_id,
    )


def _nodes_connected(network: Network, ends: tuple[str, str], ignore_switch: str | None) -> bool:
    terminals = {item.id: item for item in network.terminals}
    adjacent: dict[str, set[str]] = {}

    def link(left: str, right: str) -> None:
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)

    for switch in network.switches:
        if not switch.closed or switch.id == ignore_switch:
            continue
        link(*_switch_nodes(network, switch))
    for line in network.lines:
        link(
            terminals[line.terminal_from].connectivity_node_id,
            terminals[line.terminal_to].connectivity_node_id,
        )
    for transformer in network.transformers:
        nodes = [terminals[winding.terminal_id].connectivity_node_id for winding in transformer.windings]
        for left in nodes:
            for right in nodes:
                if left != right:
                    link(left, right)
    start, goal = ends
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        for nxt in adjacent.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _equipment_current(result: SolveResult, equipment_id: str) -> float:
    current = 0.0
    for flow in result.flows:
        if flow.equipment_id == equipment_id and flow.phase in {"A", "B", "C"} and flow.terminal_index == 0:
            current = max(current, abs(flow.current))
    return current


def replay_trace(network: Network, scenario: Any, trace: list[dict[str, Any]], solve: SolveFn | None = None) -> Engine:
    """Повтор записанных действий без вызова контроллера."""
    engine = Engine(network.model_copy(deep=True), scenario, solve=solve)
    engine.reset(seed=getattr(scenario, "seed", None))
    for step in trace:
        action = step.get("action") or {"kind": "hold"}
        origin = step.get("origin") or "controller"
        engine.step_control(action, origin=origin)
        if engine.terminated or engine.truncated:
            break
    return engine


def build_observation(engine: Engine) -> dict[str, Any]:
    scenario = engine.scenario
    visible = []
    for switch in engine.net.switches:
        if not switch.telemetered:
            continue
        visible.append(
            {
                "id": switch.id,
                "kind": switch.kind,
                "closed": switch.closed,
                "operable": switch.operable,
                "locked": switch.locked,
                "tagged_for_maintenance": switch.tagged_for_maintenance,
            }
        )
    voltages = []
    latest: dict[str, dict[str, Any]] = {}
    for sample in engine.measurements:
        if sample["available_s"] <= engine.time_s + 1e-12:
            latest[sample["meter_id"]] = sample
    for sample in latest.values():
        for reading in sample["readings"]:
            voltages.append(
                {
                    "meter_id": sample["meter_id"],
                    "node_id": sample["node_id"],
                    "phase": reading["phase"],
                    "v_pu": reading["v_pu"],
                    "angle_rad": reading["angle_rad"],
                    "produced_s": sample["produced_s"],
                    "available_s": sample["available_s"],
                }
            )
    mask = []
    for switch in engine.net.switches:
        for command in ("open", "close"):
            decision = engine.validate_action(
                {"kind": "switch", "equipment_id": switch.id, "command": command},
                enforce_hidden_current=False,
            )
            mask.append(
                {
                    "equipment_id": switch.id,
                    "command": command,
                    "allowed": decision["accepted"] and switch.operable,
                    "reason": decision["reason"],
                }
            )
    fault = scenario.fault_line_id if getattr(scenario, "fault_disclosed", False) else None
    return {
        "schema_version": "power-grid-env.observation/v1",
        "time_s": engine.time_s,
        "source": "observation",
        "switches": visible,
        "voltages": voltages,
        "known_fault_section": fault,
        "action_mask": mask,
        "limits": {"v_min_pu": scenario.v_min_pu, "v_max_pu": scenario.v_max_pu},
        "public_scenario_id": scenario.id,
    }


def agent_model(network: Network, scenario: Any) -> Network:
    """Модель, которую разрешено отдать алгоритму. Скрытый масштаб линий сюда не входит."""
    model = network.model_copy(deep=True)
    model.study_faults = []
    scales = getattr(scenario, "model_mismatch_scale", None) or {}
    for line in model.lines:
        scale = scales.get(line.id)
        if scale is None:
            continue
        line.r_ohm = [[value * scale for value in row] for row in line.r_ohm]
        line.x_ohm = [[value * scale for value in row] for row in line.x_ohm]
        line.provenance = Provenance(
            source="model-mismatch",
            quality="assumed",
            note="масштаб задан сценарием только в модели агента",
        )
    return model

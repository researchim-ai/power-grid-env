"""Многошинный AC OPF. Локальный SLSQP, без заявки на глобальный оптимум.

Допустимость проверяется отдельно: балансы пересчитываются из Ybus и найденных
напряжений, а не из сообщения оптимизатора.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize


@dataclass(frozen=True)
class Bus:
    id: int
    type_code: int
    pd: float
    qd: float
    gs: float
    bs: float
    vm: float
    va_deg: float
    vmin: float
    vmax: float


@dataclass(frozen=True)
class GeneratorSpec:
    bus: int
    qmin: float
    qmax: float
    pmin: float
    pmax: float
    cost: tuple[float, ...]
    pg: float | None = None
    qg: float | None = None


@dataclass(frozen=True)
class Branch:
    fbus: int
    tbus: int
    r: float
    x: float
    b: float
    rate_a: float
    ratio: float
    angle_deg: float
    status: int
    angmin_deg: float
    angmax_deg: float


@dataclass(frozen=True)
class OpfCase:
    name: str
    base_mva: float
    buses: list[Bus]
    generators: list[GeneratorSpec]
    branches: list[Branch]
    published_cost: float | None = None


def solve_ac_opf(case: OpfCase) -> dict[str, Any]:
    """Локальный минимум. Якобиан аналитический: конечные разности на 118 шинах не сходятся за минуты.

    global_optimum_claimed всегда False.
    """
    prepared = _prepare(case)

    def cost(z: np.ndarray) -> float:
        _vm, _theta, pg, _qg = prepared.unpack(z)
        total = 0.0
        for value, generator in zip(pg, case.generators, strict=True):
            term, _deriv = _polynomial(generator.cost, value)
            total += term
        return total

    def cost_jac(z: np.ndarray) -> np.ndarray:
        _vm, _theta, pg, _qg = prepared.unpack(z)
        gradient = np.zeros(prepared.n_var)
        for index, (value, generator) in enumerate(zip(pg, case.generators, strict=True)):
            _term, deriv = _polynomial(generator.cost, value)
            gradient[prepared.pg_at + index] = deriv
        return gradient

    def balance(z: np.ndarray) -> np.ndarray:
        vm, theta, pg, qg = prepared.unpack(z)
        return _mismatch(vm, theta, pg, qg, prepared)

    def balance_jac(z: np.ndarray) -> np.ndarray:
        vm, theta, _pg, _qg = prepared.unpack(z)
        return _mismatch_jacobian(vm, theta, prepared)

    def limits(z: np.ndarray) -> np.ndarray:
        vm, theta, _pg, _qg = prepared.unpack(z)
        return _inequalities(vm, theta, prepared)

    def limits_jac(z: np.ndarray) -> np.ndarray:
        vm, theta, _pg, _qg = prepared.unpack(z)
        return _inequality_jacobian(vm, theta, prepared)

    solved = minimize(
        cost,
        prepared.start,
        method="SLSQP",
        jac=cost_jac,
        bounds=prepared.bounds,
        constraints=[
            {"type": "eq", "fun": balance, "jac": balance_jac},
            {"type": "ineq", "fun": limits, "jac": limits_jac},
        ],
        tol=1e-8,
        options={"maxiter": 200, "ftol": 1e-10},
    )
    point = solved.x if solved.x is not None else prepared.start
    vm, theta, pg, qg = prepared.unpack(point)
    audit = audit_feasibility(case, vm, theta, pg, qg)
    feasible = bool(solved.success) and audit["feasible"]
    return {
        "success": feasible,
        "status": "locally_feasible" if feasible else "not_feasible",
        "cost": float(cost(point)) if feasible else None,
        "published_cost": case.published_cost,
        "pg_mw": pg.tolist(),
        "qg_mvar": qg.tolist(),
        "vm_pu": vm.tolist(),
        "va_deg": np.degrees(theta).tolist(),
        "message": str(solved.message),
        "iterations": int(solved.nit),
        "audit": audit,
        "global_optimum_claimed": False,
    }


@dataclass
class _Prepared:
    case: OpfCase
    index: dict[int, int]
    ybus: np.ndarray
    slack: int
    n_bus: int
    n_var: int
    theta_column: np.ndarray
    gen_bus: np.ndarray
    pd: np.ndarray
    qd: np.ndarray
    base: float
    pg_at: int
    qg_at: int
    bounds: list[tuple[float, float]]
    start: np.ndarray
    limited: list[Branch]
    angled: list[Branch]

    def unpack(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        vm = z[: self.n_bus]
        theta = np.zeros(self.n_bus)
        for bus, column in enumerate(self.theta_column):
            if column >= 0:
                theta[bus] = z[column]
        n_gen = len(self.gen_bus)
        pg = z[self.pg_at : self.pg_at + n_gen]
        qg = z[self.qg_at : self.qg_at + n_gen]
        return vm, theta, pg, qg


def _prepare(case: OpfCase) -> _Prepared:
    ids = [bus.id for bus in case.buses]
    index = {bus_id: position for position, bus_id in enumerate(ids)}
    n_bus = len(ids)
    slack = next(position for position, bus in enumerate(case.buses) if bus.type_code == 3)
    theta_column = np.full(n_bus, -1, dtype=int)
    cursor = n_bus
    for bus in range(n_bus):
        if bus == slack:
            continue
        theta_column[bus] = cursor
        cursor += 1
    n_gen = len(case.generators)
    pg_at = cursor
    qg_at = cursor + n_gen
    n_var = qg_at + n_gen
    slack_angle = math.radians(case.buses[slack].va_deg)
    start = np.zeros(n_var)
    bounds: list[tuple[float, float]] = []
    for bus in case.buses:
        start[len(bounds)] = min(max(bus.vm, bus.vmin), bus.vmax)
        bounds.append((bus.vmin, bus.vmax))
    for bus, column in enumerate(theta_column):
        if column < 0:
            continue
        angle = math.radians(case.buses[bus].va_deg) - slack_angle
        start[column] = min(math.pi, max(-math.pi, angle))
        bounds.append((-math.pi, math.pi))
    total_load = sum(bus.pd for bus in case.buses)
    capacity = [max(gen.pmax, 0.0) for gen in case.generators]
    cap_sum = sum(capacity) or 1.0
    for offset, (gen, cap) in enumerate(zip(case.generators, capacity, strict=True)):
        if gen.pg is None:
            value = total_load * cap / cap_sum
        else:
            value = gen.pg
        start[pg_at + offset] = min(gen.pmax, max(gen.pmin, value))
        bounds.append((gen.pmin, gen.pmax))
    for offset, gen in enumerate(case.generators):
        value = 0.0 if gen.qg is None else gen.qg
        start[qg_at + offset] = min(gen.qmax, max(gen.qmin, value))
        bounds.append((gen.qmin, gen.qmax))
    limited = [branch for branch in case.branches if branch.status == 1 and 0 < branch.rate_a < 1e5]
    angled = [branch for branch in case.branches if branch.status == 1]
    return _Prepared(
        case=case,
        index=index,
        ybus=_ybus(case, index),
        slack=slack,
        n_bus=n_bus,
        n_var=n_var,
        theta_column=theta_column,
        gen_bus=np.array([index[gen.bus] for gen in case.generators], dtype=int),
        pd=np.array([bus.pd for bus in case.buses], dtype=float),
        qd=np.array([bus.qd for bus in case.buses], dtype=float),
        base=case.base_mva,
        pg_at=pg_at,
        qg_at=qg_at,
        bounds=bounds,
        start=start,
        limited=limited,
        angled=angled,
    )


def _polynomial(coeffs: tuple[float, ...], value: float) -> tuple[float, float]:
    """Старшая степень первая, как в MATPOWER. Возвращает значение и производную."""
    power = 0.0
    deriv = 0.0
    for coeff in coeffs:
        deriv = deriv * value + power
        power = power * value + coeff
    return power, deriv


def _mismatch(vm: np.ndarray, theta: np.ndarray, pg: np.ndarray, qg: np.ndarray, prepared: _Prepared) -> np.ndarray:
    p_calc, q_calc = _injections(vm, theta, prepared.ybus)
    p_gen = np.zeros(prepared.n_bus)
    q_gen = np.zeros(prepared.n_bus)
    np.add.at(p_gen, prepared.gen_bus, pg)
    np.add.at(q_gen, prepared.gen_bus, qg)
    base = prepared.base
    p_mis = p_calc - (p_gen - prepared.pd) / base
    q_mis = q_calc - (q_gen - prepared.qd) / base
    stacked = np.empty(2 * prepared.n_bus)
    stacked[0::2] = p_mis
    stacked[1::2] = q_mis
    return stacked


def _state_derivatives(vm: np.ndarray, theta: np.ndarray, ybus: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """dP/dtheta, dQ/dtheta, dP/dvm, dQ/dvm. Столбец — переменная, строка — шина."""
    voltage = vm * np.exp(1j * theta)
    current = ybus @ voltage
    scale = np.divide(voltage, vm, out=np.zeros_like(voltage), where=vm > 1e-8)
    d_current_theta = ybus * (1j * voltage)[np.newaxis, :]
    d_current_vm = ybus * scale[np.newaxis, :]
    eye = np.eye(len(vm))
    d_s_theta = (1j * voltage * np.conjugate(current))[:, np.newaxis] * eye + voltage[:, np.newaxis] * np.conjugate(d_current_theta)
    d_s_vm = (scale * np.conjugate(current))[:, np.newaxis] * eye + voltage[:, np.newaxis] * np.conjugate(d_current_vm)
    return d_s_theta.real, d_s_theta.imag, d_s_vm.real, d_s_vm.imag


def _mismatch_jacobian(vm: np.ndarray, theta: np.ndarray, prepared: _Prepared) -> np.ndarray:
    d_p_theta, d_q_theta, d_p_vm, d_q_vm = _state_derivatives(vm, theta, prepared.ybus)
    jac = np.zeros((2 * prepared.n_bus, prepared.n_var))
    jac[0::2, : prepared.n_bus] = d_p_vm
    jac[1::2, : prepared.n_bus] = d_q_vm
    for bus, column in enumerate(prepared.theta_column):
        if column < 0:
            continue
        jac[0::2, column] = d_p_theta[:, bus]
        jac[1::2, column] = d_q_theta[:, bus]
    base = prepared.base
    for gen, bus in enumerate(prepared.gen_bus):
        jac[2 * bus, prepared.pg_at + gen] -= 1.0 / base
        jac[2 * bus + 1, prepared.qg_at + gen] -= 1.0 / base
    return jac


def _branch_complex(vm: np.ndarray, theta: np.ndarray, branch: Branch, index: dict[int, int], base: float) -> tuple[complex, complex]:
    y_ff, y_ft, y_tf, y_tt = _branch_quad(branch)
    src = index[branch.fbus]
    dst = index[branch.tbus]
    voltage_src = vm[src] * np.exp(1j * theta[src])
    voltage_dst = vm[dst] * np.exp(1j * theta[dst])
    power_src = voltage_src * np.conjugate(y_ff * voltage_src + y_ft * voltage_dst) * base
    power_dst = voltage_dst * np.conjugate(y_tt * voltage_dst + y_tf * voltage_src) * base
    return complex(power_src), complex(power_dst)


def _inequalities(vm: np.ndarray, theta: np.ndarray, prepared: _Prepared) -> np.ndarray:
    values = []
    for branch in prepared.limited:
        src, dst = _branch_complex(vm, theta, branch, prepared.index, prepared.base)
        values.append(branch.rate_a - abs(src))
        values.append(branch.rate_a - abs(dst))
    for branch in prepared.angled:
        i = prepared.index[branch.fbus]
        j = prepared.index[branch.tbus]
        delta = math.degrees(theta[i] - theta[j])
        values.append(delta - branch.angmin_deg)
        values.append(branch.angmax_deg - delta)
    return np.array(values if values else [0.0])


def _flow_gradient(power: complex, d_power: np.ndarray) -> np.ndarray:
    magnitude = abs(power)
    if magnitude < 1e-8:
        return np.zeros(d_power.shape[0])
    return np.real(np.conjugate(power) * d_power) / magnitude


def _inequality_jacobian(vm: np.ndarray, theta: np.ndarray, prepared: _Prepared) -> np.ndarray:
    rows = 2 * len(prepared.limited) + 2 * len(prepared.angled)
    jac = np.zeros((max(rows, 1), prepared.n_var))
    row = 0
    voltage = vm * np.exp(1j * theta)
    for branch in prepared.limited:
        y_ff, y_ft, y_tf, y_tt = _branch_quad(branch)
        src = prepared.index[branch.fbus]
        dst = prepared.index[branch.tbus]
        voltage_src = voltage[src]
        voltage_dst = voltage[dst]
        current_src = y_ff * voltage_src + y_ft * voltage_dst
        current_dst = y_tt * voltage_dst + y_tf * voltage_src
        power_src = voltage_src * np.conjugate(current_src) * prepared.base
        power_dst = voltage_dst * np.conjugate(current_dst) * prepared.base
        d_src = np.zeros(prepared.n_var, dtype=complex)
        d_dst = np.zeros(prepared.n_var, dtype=complex)
        for variable, d_voltage in (
            (src, voltage_src / vm[src] if vm[src] > 1e-8 else 0.0),
            (int(prepared.theta_column[src]), 1j * voltage_src),
        ):
            if variable < 0 or d_voltage == 0:
                continue
            d_src[variable] = (d_voltage * np.conjugate(current_src) + voltage_src * np.conjugate(y_ff * d_voltage)) * prepared.base
            d_dst[variable] = voltage_dst * np.conjugate(y_tf * d_voltage) * prepared.base
        for variable, d_voltage in (
            (dst, voltage_dst / vm[dst] if vm[dst] > 1e-8 else 0.0),
            (int(prepared.theta_column[dst]), 1j * voltage_dst),
        ):
            if variable < 0 or d_voltage == 0:
                continue
            d_src[variable] = voltage_src * np.conjugate(y_ft * d_voltage) * prepared.base
            d_dst[variable] = (d_voltage * np.conjugate(current_dst) + voltage_dst * np.conjugate(y_tt * d_voltage)) * prepared.base
        jac[row] = -_flow_gradient(complex(power_src), d_src)
        jac[row + 1] = -_flow_gradient(complex(power_dst), d_dst)
        row += 2
    degree = 180.0 / math.pi
    for branch in prepared.angled:
        i = prepared.index[branch.fbus]
        j = prepared.index[branch.tbus]
        for column, sign in ((int(prepared.theta_column[i]), 1.0), (int(prepared.theta_column[j]), -1.0)):
            if column < 0:
                continue
            jac[row, column] = sign * degree
            jac[row + 1, column] = -sign * degree
        row += 2
    return jac


def audit_feasibility(
    case: OpfCase,
    vm: np.ndarray,
    theta: np.ndarray,
    pg: np.ndarray,
    qg: np.ndarray,
    *,
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    """Независимая проверка: те же Ybus-формулы, без обращения к SLSQP."""
    index = {bus.id: position for position, bus in enumerate(case.buses)}
    ybus = _ybus(case, index)
    p_calc, q_calc = _injections(vm, theta, ybus)
    worst = 0.0
    for bus, spec in enumerate(case.buses):
        p_gen = sum(pg[k] for k, gen in enumerate(case.generators) if index[gen.bus] == bus)
        q_gen = sum(qg[k] for k, gen in enumerate(case.generators) if index[gen.bus] == bus)
        worst = max(worst, abs(p_calc[bus] - (p_gen - spec.pd) / case.base_mva))
        worst = max(worst, abs(q_calc[bus] - (q_gen - spec.qd) / case.base_mva))
        if not spec.vmin - 1e-6 <= vm[bus] <= spec.vmax + 1e-6:
            worst = max(worst, 1.0)
    for value, generator in zip(pg, case.generators, strict=True):
        if value < generator.pmin - 1e-4 or value > generator.pmax + 1e-4:
            worst = max(worst, 1.0)
    for value, generator in zip(qg, case.generators, strict=True):
        if value < generator.qmin - 1e-3 or value > generator.qmax + 1e-3:
            worst = max(worst, 1.0)
    flow_ok = True
    angle_ok = True
    for branch in case.branches:
        if branch.status != 1:
            continue
        separation = math.degrees(theta[index[branch.fbus]] - theta[index[branch.tbus]])
        if separation < branch.angmin_deg - 1e-3 or separation > branch.angmax_deg + 1e-3:
            angle_ok = False
        if branch.rate_a <= 0 or branch.rate_a >= 1e5:
            continue
        if any(flow > branch.rate_a + 1e-3 for flow in _branch_mva(vm, theta, branch, index, case.base_mva)):
            flow_ok = False
    return {
        "max_mismatch_pu": worst,
        "flows_within_rating": flow_ok,
        "angles_within_limit": angle_ok,
        "feasible": worst <= tolerance and flow_ok and angle_ok,
    }


def _ybus(case: OpfCase, index: dict[int, int]) -> np.ndarray:
    size = len(case.buses)
    matrix = np.zeros((size, size), dtype=complex)
    for branch in case.branches:
        if branch.status != 1:
            continue
        y_ff, y_ft, y_tf, y_tt = _branch_quad(branch)
        i = index[branch.fbus]
        j = index[branch.tbus]
        matrix[i, i] += y_ff
        matrix[i, j] += y_ft
        matrix[j, i] += y_tf
        matrix[j, j] += y_tt
    for bus in case.buses:
        matrix[index[bus.id], index[bus.id]] += complex(bus.gs, bus.bs)
    return matrix


def _branch_quad(branch: Branch) -> tuple[complex, complex, complex, complex]:
    series = 1.0 / complex(branch.r, branch.x)
    ratio = 1.0 if branch.ratio == 0.0 else branch.ratio
    tap = ratio * np.exp(1j * math.radians(branch.angle_deg))
    shunt = 1j * branch.b / 2.0
    y_ff = (series + shunt) / (tap * np.conjugate(tap))
    y_ft = -series / np.conjugate(tap)
    y_tf = -series / tap
    y_tt = series + shunt
    return y_ff, y_ft, y_tf, y_tt


def _injections(vm: np.ndarray, theta: np.ndarray, ybus: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    voltage = vm * np.exp(1j * theta)
    current = ybus @ voltage
    power = voltage * np.conjugate(current)
    return power.real, power.imag


def _branch_mva(
    vm: np.ndarray, theta: np.ndarray, branch: Branch, index: dict[int, int], base_mva: float
) -> tuple[float, float]:
    """|S| обоих концов ветви. Лимит PGLib действует на каждый конец."""
    y_ff, y_ft, y_tf, y_tt = _branch_quad(branch)
    src = index[branch.fbus]
    dst = index[branch.tbus]
    voltage_src = vm[src] * np.exp(1j * theta[src])
    voltage_dst = vm[dst] * np.exp(1j * theta[dst])
    power_src = voltage_src * np.conjugate(y_ff * voltage_src + y_ft * voltage_dst) * base_mva
    power_dst = voltage_dst * np.conjugate(y_tt * voltage_dst + y_tf * voltage_src) * base_mva
    return float(abs(power_src)), float(abs(power_dst))

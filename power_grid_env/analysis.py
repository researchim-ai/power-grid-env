"""DC-поток, PTDF/LODF, оценка состояния, локальный AC OPF и N-1.

DC не подтверждает напряжения. AC OPF здесь — локальный SLSQP, без заявки
глобального оптимума. Положительная последовательность не доказывает несимметрию.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import linprog, minimize

from power_grid_env.backend_opendss import solve_network
from power_grid_env.model import Network


def dc_power_flow(susceptance: np.ndarray, injection_pu: np.ndarray, slack: int) -> np.ndarray:
    """B' θ = P. Угол опоры равен нулю. Вырожденная сеть без опоры не решается подменой."""
    size = len(injection_pu)
    if abs(injection_pu.sum()) > 1e-8:
        raise ValueError("инъекции DC не сбалансированы: потери этой моделью не представляются")
    unknown = [index for index in range(size) if index != slack]
    matrix = susceptance[np.ix_(unknown, unknown)]
    try:
        angles = np.linalg.solve(matrix, injection_pu[unknown])
    except np.linalg.LinAlgError as exc:
        raise ValueError("DC-матрица вырождена: остров или нулевое сопротивление") from exc
    theta = np.zeros(size)
    theta[unknown] = angles
    return theta


def line_flow_pu(theta: np.ndarray, frm: int, to: int, x_pu: float) -> float:
    return (theta[frm] - theta[to]) / x_pu


def ptdf_column(susceptance: np.ndarray, slack: int, lines: list[tuple[int, int, float]], injection_bus: int) -> np.ndarray:
    size = susceptance.shape[0]
    injection = np.zeros(size)
    injection[injection_bus] = 1.0
    injection[slack] -= 1.0
    theta = dc_power_flow(susceptance, injection, slack)
    return np.array([line_flow_pu(theta, frm, to, x_pu) for frm, to, x_pu in lines])


def lodf(ptdf_outage: float, ptdf_monitored: float) -> float:
    if abs(1.0 - ptdf_outage) < 1e-9:
        raise ValueError("LODF неприменим: отключение делит сеть")
    return ptdf_monitored / (1.0 - ptdf_outage)


def wls_dc(measurements: np.ndarray, h_matrix: np.ndarray, variances: np.ndarray) -> dict[str, Any]:
    weight = np.diag(1.0 / variances)
    gram = h_matrix.T @ weight @ h_matrix
    if np.linalg.matrix_rank(gram) < gram.shape[0]:
        return {"observable": False, "estimate": None, "residuals": None}
    estimate = np.linalg.solve(gram, h_matrix.T @ weight @ measurements)
    residual = measurements - h_matrix @ estimate
    normalized = residual / np.sqrt(variances)
    return {"observable": True, "estimate": estimate, "residuals": residual, "normalized": normalized}


def dc_opf(costs: np.ndarray, pmin: np.ndarray, pmax: np.ndarray, load_pu: float) -> dict[str, Any]:
    size = len(costs)
    result = linprog(costs, bounds=list(zip(pmin, pmax)), A_eq=np.ones((1, size)), b_eq=[load_pu], method="highs")
    return {"success": bool(result.success), "generation_pu": None if result.x is None else result.x, "cost": None if result.fun is None else float(result.fun), "status": result.message}


def ac_opf_two_bus(load_p: float, load_q: float, x_pu: float, cost: float, pmax: float, vmax: float = 1.05) -> dict[str, Any]:
    """Локальный AC OPF двух узлов. Pg связан с перетоком и нагрузкой.

    Узел 1 — опора, V=1∠0. Узел 2 — нагрузка, напряжение V∠−theta.
    Потери активной мощности на чистом x равны нулю, поэтому Pg = Pнагр.
    Это локальный SLSQP, не доказанный глобальный оптимум.
    """

    def received(z: np.ndarray) -> tuple[float, float]:
        _pg, _qg, voltage, theta = z
        active = voltage * math_sin(theta) / x_pu
        reactive = (voltage * math_cos(theta) - voltage * voltage) / x_pu
        return active, reactive

    def objective(z: np.ndarray) -> float:
        return cost * z[0]

    constraints = [
        {"type": "eq", "fun": lambda z: received(z)[0] - load_p},
        {"type": "eq", "fun": lambda z: received(z)[1] - load_q},
        {"type": "eq", "fun": lambda z: z[0] - load_p},
    ]
    bounds = [(0.0, pmax), (-2.0, 2.0), (0.9, vmax), (0.0, 0.6)]
    theta0 = min(0.5, x_pu * max(load_p, 1e-6))
    solved = minimize(
        objective,
        np.array([load_p, load_q, 1.0, theta0]),
        bounds=bounds,
        constraints=constraints,
        method="SLSQP",
        tol=1e-12,
    )
    generation = None if solved.x is None else float(solved.x[0])
    feasible = bool(solved.success) and generation is not None and abs(generation - load_p) <= 1e-4
    return {
        "success": feasible,
        "x": None if solved.x is None else solved.x.tolist(),
        "cost": None if not feasible or solved.fun is None else float(solved.fun),
        "message": str(solved.message),
        "global_optimum_claimed": False,
        "pg_pu": generation,
    }


def dc_opf_network(
    *,
    n_bus: int,
    slack: int,
    lines: list[tuple[int, int, float, float]],
    generators: list[dict[str, float]],
    loads: dict[int, float],
) -> dict[str, Any]:
    """Сетевой DC OPF: углы, балансы узлов, пределы ветвей и Pmin/Pmax.

    lines: (from, to, x_pu, flow_limit_pu). generators: bus, cost, pmin, pmax.
    """
    free = [bus for bus in range(n_bus) if bus != slack]
    theta_index = {bus: index for index, bus in enumerate(free)}
    n_theta = len(free)
    n_gen = len(generators)

    def theta_coeff(bus: int, row: np.ndarray, scale: float) -> None:
        if bus == slack:
            return
        row[theta_index[bus]] += scale

    rows_eq = []
    rhs = []
    for bus in range(n_bus):
        row = np.zeros(n_theta + n_gen)
        for src, dst, reactance, _limit in lines:
            if bus == src:
                theta_coeff(src, row, -1.0 / reactance)
                theta_coeff(dst, row, 1.0 / reactance)
            elif bus == dst:
                theta_coeff(src, row, 1.0 / reactance)
                theta_coeff(dst, row, -1.0 / reactance)
        for index, generator in enumerate(generators):
            if int(generator["bus"]) == bus:
                row[n_theta + index] += 1.0
        rows_eq.append(row)
        rhs.append(float(loads.get(bus, 0.0)))
    ineq = []
    ineq_rhs = []
    for src, dst, reactance, limit in lines:
        upper = np.zeros(n_theta + n_gen)
        theta_coeff(src, upper, 1.0 / reactance)
        theta_coeff(dst, upper, -1.0 / reactance)
        ineq.append(upper)
        ineq_rhs.append(limit)
        ineq.append(-upper)
        ineq_rhs.append(limit)
    bounds = [(None, None)] * n_theta + [(float(item["pmin"]), float(item["pmax"])) for item in generators]
    cost = np.zeros(n_theta + n_gen)
    for index, generator in enumerate(generators):
        cost[n_theta + index] = float(generator["cost"])
    result = linprog(
        cost,
        A_ub=np.vstack(ineq) if ineq else None,
        b_ub=np.array(ineq_rhs) if ineq else None,
        A_eq=np.vstack(rows_eq),
        b_eq=np.array(rhs),
        bounds=bounds,
        method="highs",
    )
    generation = None if result.x is None else result.x[n_theta:]
    return {
        "success": bool(result.success),
        "status": "optimal" if result.success else "infeasible",
        "generation_pu": None if generation is None else generation.tolist(),
        "cost": None if result.fun is None or not result.success else float(result.fun),
        "message": str(result.message),
    }


def math_sin(value: float) -> float:
    import math

    return math.sin(value)


def math_cos(value: float) -> float:
    import math

    return math.cos(value)


def ac_opf_network(
    *,
    load_p: float,
    load_q: float,
    x_pu: float,
    flow_limit: float,
    pmax: float,
    cost: float = 1.0,
    vmax: float = 1.05,
) -> dict[str, Any]:
    """Двухшинный AC OPF с пределом перетока. Локальный SLSQP, не глобальный оптимум."""

    def received(z: np.ndarray) -> tuple[float, float]:
        voltage, theta = float(z[2]), float(z[3])
        active = voltage * math_sin(theta) / x_pu
        reactive = (voltage * math_cos(theta) - voltage * voltage) / x_pu
        return active, reactive

    constraints = [
        {"type": "eq", "fun": lambda z: received(z)[0] - load_p},
        {"type": "eq", "fun": lambda z: received(z)[1] - load_q},
        {"type": "eq", "fun": lambda z: z[0] - load_p},
        {"type": "ineq", "fun": lambda z: flow_limit - received(z)[0]},
        {"type": "ineq", "fun": lambda z: flow_limit + received(z)[0]},
    ]
    bounds = [(0.0, pmax), (-2.0, 2.0), (0.9, vmax), (0.0, 0.6)]
    theta0 = min(0.5, x_pu * max(load_p, 1e-6))
    solved = minimize(
        lambda z: cost * z[0],
        np.array([min(load_p, pmax), load_q, 1.0, theta0]),
        bounds=bounds,
        constraints=constraints,
        method="SLSQP",
        tol=1e-12,
    )
    generation = None if solved.x is None else float(solved.x[0])
    within_flow = generation is not None and abs(load_p) <= flow_limit + 1e-6
    feasible = bool(solved.success) and generation is not None and abs(generation - load_p) <= 1e-4 and within_flow
    return {
        "success": feasible,
        "status": "optimal" if feasible else "infeasible",
        "pg_pu": generation,
        "message": str(solved.message),
        "global_optimum_claimed": False,
    }


def n1_transformer_outages(network: Network, solve=solve_network) -> list[dict[str, Any]]:
    """Отключение каждого трансформатора остаётся в отчёте, в том числе без решения."""
    reports = []
    for transformer in network.transformers:
        trial = network.model_copy(deep=True)
        trial.transformers = [item for item in trial.transformers if item.id != transformer.id]
        trial.terminals = [item for item in trial.terminals if item.equipment_id != transformer.id]
        try:
            result = solve(trial)
            reports.append(
                {
                    "outage": transformer.id,
                    "status": result.status,
                    "converged": result.converged,
                    "collapse": False,
                }
            )
        except Exception as exc:
            reports.append({"outage": transformer.id, "status": "unsolved", "error": str(exc), "collapse": False})
    return reports


def n1_line_outages(network: Network, solve=solve_network) -> list[dict[str, Any]]:
    reports = []
    for line in network.lines:
        trial = network.model_copy(deep=True)
        trial.lines = [item for item in trial.lines if item.id != line.id]
        trial.terminals = [item for item in trial.terminals if item.equipment_id != line.id]
        try:
            result = solve(trial)
            overloaded = []
            if result.status == "converged":
                for other in trial.lines:
                    current = max(
                        (abs(flow.current) for flow in result.flows if flow.equipment_id == other.id and flow.phase in "ABC" and flow.terminal_index == 0),
                        default=0.0,
                    )
                    if current > other.rating_a:
                        overloaded.append(other.id)
            reports.append(
                {
                    "outage": line.id,
                    "status": result.status,
                    "converged": result.converged,
                    "overloads": overloaded,
                    "collapse": False,
                }
            )
        except Exception as exc:
            reports.append({"outage": line.id, "status": "unsolved", "error": str(exc), "collapse": False})
    return reports


def voltage_sensitivity(network: Network, load_id: str, delta_w: float, solve=solve_network) -> dict[str, float]:
    base = solve(network)
    bumped = network.model_copy(deep=True)
    for load in bumped.loads:
        if load.id == load_id:
            for phase in load.p_w:
                load.p_w[phase] += delta_w / len(load.p_w)
    changed = solve(bumped)
    if base.status != "converged" or changed.status != "converged":
        return {"valid": False, "reason": "solve_not_converged"}
    sensitivities = {}
    index = {(item.bus_id, item.phase): item for item in base.voltages}
    for item in changed.voltages:
        if item.phase not in "ABC" or not item.energized:
            continue
        previous = index[(item.bus_id, item.phase)]
        sensitivities[f"{item.bus_id}:{item.phase}"] = (abs(item.complex) - abs(previous.complex)) / delta_w
    return {"valid": True, "dV_dP": sensitivities}

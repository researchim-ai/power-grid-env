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
    """Локальная AC-постановка двух узлов. Не глобальный оптимум."""

    def objective(z: np.ndarray) -> float:
        return cost * z[0]

    def power(z: np.ndarray) -> tuple[float, float]:
        pg, qg, v, theta = z
        p = v * math_sin(theta) / x_pu
        q = (v * v - v * math_cos(theta)) / x_pu
        return p, q

    constraints = [
        {"type": "eq", "fun": lambda z: power(z)[0] - load_p},
        {"type": "eq", "fun": lambda z: power(z)[1] - load_q},
    ]
    bounds = [(0.0, pmax), (-2.0, 2.0), (0.95, vmax), (-0.5, 0.5)]
    solved = minimize(objective, np.array([load_p, load_q, 1.0, 0.05]), bounds=bounds, constraints=constraints, method="SLSQP")
    return {
        "success": bool(solved.success),
        "x": None if solved.x is None else solved.x.tolist(),
        "cost": None if solved.fun is None else float(solved.fun),
        "message": solved.message,
        "global_optimum_claimed": False,
    }


def math_sin(value: float) -> float:
    import math

    return math.sin(value)


def math_cos(value: float) -> float:
    import math

    return math.cos(value)


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

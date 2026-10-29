"""Интерфейс ANDES: классическая машина и объявленная схема Кундура.

Промышленный сертификат RMS этим файлом не выдаётся.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from power_grid_env.dynamics import _implicit_trapezoid, equilibrium, simulate_smib, tgov1_power

_MACHINE_BASE = 8.0
_INERTIA_S = _MACHINE_BASE / 2.0
_INTERNAL_X = 0.3
_LINE_X = 0.2
_POWER = 0.5
_TERMINAL_V = 1.05
_INFINITE_V = 1.0
_FREQUENCY_HZ = 50.0


def ensure_andes_importable() -> bool:
    """Подключает уже скачанную библиотеку, если она лежит рядом с проектом."""
    if importlib.util.find_spec("andes") is not None:
        return True
    bundled = Path(__file__).resolve().parents[1] / ".andes-deps"
    if not bundled.is_dir():
        return False
    sys.path.append(str(bundled))
    return importlib.util.find_spec("andes") is not None


def qualify_classical_machine() -> dict[str, Any]:
    """Инициализация из режима и качание классической машины против аналитической модели.

    ANDES M = 2H. Угол внутреннего напряжения сверяется с asin(Pm·X/(E·V)).
    Промышленный многомашинный RMS с регуляторами этим не открывается.
    """
    if not ensure_andes_importable():
        raise ImportError("ANDES не установлен")
    import andes

    logging.getLogger("andes").setLevel(logging.ERROR)
    steady = _system(andes, 0.01)
    steady.TDS.config.tf = 0.5
    steady.TDS.init()
    angle_error = abs(_rotor_angle(steady) - _analytic_angle(steady))
    residual = float(np.max(np.abs(steady.dae.fg)))
    steady.TDS.run()
    quiet_angle, quiet_speed = _swing(steady)
    coarse = _disturbed(andes, 0.0005)
    fine = _disturbed(andes, 0.00025)
    reasons = []
    if not steady.PFlow.converged or not steady.TDS.test_ok:
        reasons.append("initialization")
    if angle_error > 1e-6:
        reasons.append("angle")
    if residual > 1e-8:
        reasons.append("residual")
    if quiet_angle > 1e-5 or quiet_speed > 1e-6:
        reasons.append("undisturbed")
    if not fine["error_rad"] < coarse["error_rad"]:
        reasons.append("step")
    if fine["error_rad"] > 1e-4:
        reasons.append("trajectory")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "andes_version": andes.__version__,
        "angle_error_rad": angle_error,
        "init_residual": residual,
        "undisturbed_angle_rad": quiet_angle,
        "undisturbed_speed_pu": quiet_speed,
        "coarse_error_rad": coarse["error_rad"],
        "trajectory_error_rad": fine["error_rad"],
        "declared_model": "classical_smib",
        "industrial_rms_qualified": False,
    }


def _disturbed(andes: Any, step_s: float) -> dict[str, float]:
    system = _system(andes, step_s)
    system.TDS.config.tf = 0.2
    system.TDS.init()
    assert system.TDS.test_ok
    angle = _rotor_angle(system)
    address = _address(system, "delta")
    system.dae.x[address] += 0.05
    system.TDS.run()
    time, delta, _speed = _series(system)
    reference = simulate_smib(
        pm=_POWER,
        e_pu=_internal_voltage(system),
        v_pu=_INFINITE_V,
        x_pu=_INTERNAL_X + _LINE_X,
        inertia_s=_INERTIA_S,
        damping=0.0,
        frequency_hz=_FREQUENCY_HZ,
        dt_s=step_s,
        t_end_s=0.2,
        delta0=angle + 0.05,
        omega0=2.0 * math.pi * _FREQUENCY_HZ,
    )
    compared = np.interp(time, reference["t"], reference["delta"])
    return {"error_rad": float(np.max(np.abs(compared - delta)))}


def _system(andes: Any, step_s: float) -> Any:
    pycode = Path(tempfile.gettempdir()) / "power-grid-env-andes-pycode"
    pycode.mkdir(exist_ok=True)
    system = andes.System(no_output=True, default_config=True, pycode_path=str(pycode))
    system.config.freq = _FREQUENCY_HZ
    system.add("Bus", idx=1, Vn=110, v0=_TERMINAL_V)
    system.add("Bus", idx=2, Vn=110, v0=_INFINITE_V)
    system.add("PV", idx="PV1", bus=1, Sn=100, Vn=110, p0=_POWER, v0=_TERMINAL_V)
    system.add("Slack", idx="SL1", bus=2, Sn=100, Vn=110, v0=_INFINITE_V, a0=0.0)
    system.add("Line", idx="L1", bus1=1, bus2=2, Sn=100, fn=_FREQUENCY_HZ, Vn1=110, Vn2=110, r=0.0, x=_LINE_X, b=0.0)
    system.add(
        "GENCLS",
        idx="G1",
        bus=1,
        gen="PV1",
        Sn=100,
        Vn=110,
        fn=_FREQUENCY_HZ,
        M=_MACHINE_BASE,
        D=0.0,
        ra=0.0,
        xd1=_INTERNAL_X,
        xl=0.0,
    )
    system.setup()
    system.PFlow.config.tol = 1e-12
    system.PFlow.run()
    system.TDS.config.tol = 1e-8
    system.TDS.config.tstep = step_s
    system.TDS.config.criteria = 0
    return system


def _analytic_angle(system: Any) -> float:
    omega_s = 2.0 * math.pi * _FREQUENCY_HZ
    angle, _speed = equilibrium(_POWER, _internal_voltage(system), _INFINITE_V, _INTERNAL_X + _LINE_X, omega_s)
    return angle


def _internal_voltage(system: Any) -> float:
    return float(np.asarray(system.GENCLS.vf.v).ravel()[0])


def _rotor_angle(system: Any) -> float:
    return float(np.asarray(system.GENCLS.delta.v).ravel()[0])


def _address(system: Any, name: str) -> int:
    return int(np.asarray(getattr(system.GENCLS, name).a).ravel()[0])


def _series(system: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    time = np.asarray(system.dae.ts.t, dtype=float).ravel()
    states = np.asarray(system.dae.ts.x, dtype=float)
    return time, states[:, _address(system, "delta")], states[:, _address(system, "omega")]


def _swing(system: Any) -> tuple[float, float]:
    _time, delta, speed = _series(system)
    origin = float(delta[0])
    return float(np.max(np.abs(delta - origin))), float(np.max(np.abs(speed - 1.0)))


def qualify_regulated_plant() -> dict[str, Any]:
    """Четыре машины Кундура с турбиной, возбудителем и PSS, плюс КЗ конечного сопротивления.

    События: отключение линии, отключение генератора, разделение двух районов и шаг нагрузки.
    Угол ротора обязан следовать ω. Мощность TGOV1 сверяется со структурной схемой.
    """
    if not ensure_andes_importable():
        raise ImportError("ANDES не установлен")
    import andes

    logging.getLogger("andes").setLevel(logging.ERROR)
    line = _kundur(andes, lambda system: system.Toggler.t.v.__setitem__(0, 0.2), tf_s=0.8, step_s=0.01)
    tripped = _kundur(
        andes,
        lambda system: (system.Toggler.u.v.__setitem__(0, 0), system.add("Toggle", idx="TG", model="GENROU", dev=2, t=0.2)),
        tf_s=0.5,
        step_s=0.01,
    )
    island = _kundur(
        andes,
        lambda system: (
            system.Toggler.t.v.__setitem__(0, 0.2),
            system.add("Toggle", idx="TL", model="Line", dev="Line_7", t=0.2),
        ),
        tf_s=0.6,
        step_s=0.01,
    )
    stepped = _kundur(
        andes,
        lambda system: (
            system.Toggler.u.v.__setitem__(0, 0),
            system.add("Alter", idx="AL", model="PQ", dev="PQ_0", src="p0", attr="v", method="+", amount=1.0, t=0.2),
        ),
        tf_s=0.5,
        step_s=0.01,
    )
    fault = _faulted_machine(andes)
    stabilizer = _kundur(andes, lambda system: system.Toggler.t.v.__setitem__(0, 0.2), tf_s=0.6, step_s=0.01, case="kundur/kundur_ieeest.json")
    reasons = []
    if line["init_residual"] > 1e-6 or not line["initialized"]:
        reasons.append("initialization")
    if line["quiet_speed"] > 1e-8 or line["quiet_angle"] > 1e-6:
        reasons.append("undisturbed")
    if line["angle_error"] > 1e-6 or tripped["angle_error"] > 1e-6 or island["angle_error"] > 1e-6 or fault["angle_error"] > 1e-6:
        reasons.append("angle")
    if line["governor_error"] > 1e-6:
        reasons.append("governor")
    if not line["line_open"] or line["exciter_move"] < 1e-3 or not line["exciter_inside"]:
        reasons.append("exciter")
    if not tripped["generator_off"] or not tripped["others_on"]:
        reasons.append("generator_trip")
    if not island["ties_open"] or island["area_speed_gap"] < 1e-3:
        reasons.append("island")
    if abs(stepped["load_increase"] - 1.0) > 1e-9:
        reasons.append("load")
    if fault["voltage_ratio"] > 0.8 or fault["recovered_ratio"] < 0.95:
        reasons.append("fault")
    if abs(stabilizer["pss_rest"]) > 1e-8 or not stabilizer["pss_inside"] or stabilizer["pss_error"] > 1e-6:
        reasons.append("pss")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "andes_version": andes.__version__,
        "machines": line["machines"],
        "init_residual": line["init_residual"],
        "governor_error": line["governor_error"],
        "exciter_move": line["exciter_move"],
        "area_speed_gap": island["area_speed_gap"],
        "voltage_ratio": fault["voltage_ratio"],
        "pss_error": stabilizer["pss_error"],
        "declared_model": "kundur_four_machine",
        "industrial_rms_qualified": False,
    }


def _kundur(andes: Any, prepare, *, tf_s: float, step_s: float, case: str = "kundur/kundur_full.json") -> dict[str, Any]:
    system = andes.load(andes.get_case(case), setup=False, no_output=True, default_config=True, pycode_path=_pycode())
    prepare(system)
    system.setup()
    load_at = [str(item) for item in np.asarray(system.PQ.idx.v).ravel()].index("PQ_0")
    load_before = float(np.asarray(system.PQ.p0.v).ravel()[load_at])
    if not system.PFlow.run():
        raise RuntimeError("режим Кундура не сошёлся")
    system.TDS.config.tol = 1e-6
    system.TDS.config.tstep = step_s
    system.TDS.config.tf = tf_s
    system.TDS.config.criteria = 0
    system.TDS.init()
    initialized = bool(system.TDS.test_ok)
    residual = float(np.max(np.abs(system.dae.fg)))
    system.TDS.run()
    time, speed, delta = _machine_series(system, "GENROU")
    quiet = time < 0.2
    field = _algebraic(system, system.EXDC2.vout) if system.EXDC2.n else np.zeros(len(time))
    ceiling = np.asarray(system.EXDC2.VRMAX.v).ravel()[0] if system.EXDC2.n else 0.0
    floor = np.asarray(system.EXDC2.VRMIN.v).ravel()[0] if system.EXDC2.n else 0.0
    tie_ids = [str(item) for item in np.asarray(system.Line.idx.v).ravel()]
    line_status = np.asarray(system.Line.u.v).ravel()
    machine_status = np.asarray(system.GENROU.u.v).ravel()
    machine_ids = [int(item) for item in np.asarray(system.GENROU.idx.v).ravel()]
    tripped = machine_ids.index(2)
    if hasattr(system, "IEEEST") and system.IEEEST.n:
        stabilizer = _algebraic(system, system.IEEEST.vsout)
        pss_upper = float(np.asarray(system.IEEEST.LSMAX.v).ravel()[0])
        pss_lower = float(np.asarray(system.IEEEST.LSMIN.v).ravel()[0])
    else:
        stabilizer = np.zeros(1)
        pss_upper = 0.2
        pss_lower = -0.2
    return {
        "initialized": initialized,
        "init_residual": residual,
        "machines": int(system.GENROU.n),
        "quiet_speed": float(np.max(np.abs(speed[quiet] - 1.0))),
        "quiet_angle": float(np.max(np.abs(delta[quiet] - delta[0]))),
        "angle_error": _angle_error(system, "GENROU"),
        "governor_error": _governor_error(system, time, speed[:, 0]),
        "line_open": bool(line_status[tie_ids.index("Line_8")] == 0.0),
        "ties_open": bool(line_status[tie_ids.index("Line_7")] == 0.0 and line_status[tie_ids.index("Line_8")] == 0.0),
        "exciter_move": float(np.max(np.abs(field - field[0]))) if len(field) else 0.0,
        "exciter_inside": bool(np.all(field <= ceiling + 1e-8) and np.all(field >= floor - 1e-8)) if len(field) else False,
        "generator_off": bool(machine_status[tripped] == 0.0),
        "others_on": bool(np.all(np.delete(machine_status, tripped) == 1.0)),
        "area_speed_gap": float(abs(np.mean(speed[-1, :2]) - np.mean(speed[-1, 2:]))),
        "load_increase": float(np.asarray(system.PQ.p0.v).ravel()[load_at] - load_before),
        "pss_rest": float(np.asarray(stabilizer).ravel()[0]),
        "pss_error": _pss_error(system, time, speed[:, 0], stabilizer),
        "pss_inside": bool(np.all(stabilizer <= pss_upper + 1e-9) and np.all(stabilizer >= pss_lower - 1e-9)),
    }


def _faulted_machine(andes: Any) -> dict[str, float]:
    system = andes.System(no_output=True, default_config=True, pycode_path=_pycode())
    system.config.freq = _FREQUENCY_HZ
    system.add("Bus", idx=1, Vn=110, v0=_TERMINAL_V)
    system.add("Bus", idx=2, Vn=110, v0=_INFINITE_V)
    system.add("PV", idx="PV1", bus=1, Sn=100, Vn=110, p0=_POWER, v0=_TERMINAL_V)
    system.add("Slack", idx="SL1", bus=2, Sn=100, Vn=110, v0=_INFINITE_V, a0=0.0)
    system.add("Line", idx="L1", bus1=1, bus2=2, Sn=100, fn=_FREQUENCY_HZ, Vn1=110, Vn2=110, r=0.0, x=_LINE_X, b=0.0)
    system.add("GENCLS", idx="G1", bus=1, gen="PV1", Sn=100, Vn=110, fn=_FREQUENCY_HZ, M=_MACHINE_BASE, D=1.0, ra=0.0, xd1=_INTERNAL_X, xl=0.0)
    system.add("Fault", idx="F1", bus=1, tf=0.05, tc=0.12, xf=0.2, rf=0.0)
    system.setup()
    if not system.PFlow.run():
        raise RuntimeError("режим машины с КЗ не сошёлся")
    system.TDS.config.tol = 1e-6
    system.TDS.config.tstep = 0.002
    system.TDS.config.tf = 0.3
    system.TDS.config.criteria = 0
    system.TDS.init()
    system.TDS.run()
    time = np.asarray(system.dae.ts.t, dtype=float).ravel()
    voltage = _algebraic(system, system.Bus.v)[:, 0]
    before = float(voltage[0])
    during = voltage[(time >= 0.05) & (time <= 0.12)]
    return {
        "angle_error": _angle_error(system, "GENCLS"),
        "voltage_ratio": float(np.min(during) / before),
        "recovered_ratio": float(voltage[-1] / before),
    }


def _pycode() -> str:
    path = Path(tempfile.gettempdir()) / "power-grid-env-andes-pycode"
    path.mkdir(exist_ok=True)
    return str(path)


def _machine_series(system: Any, model_name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    model = getattr(system, model_name)
    time = np.asarray(system.dae.ts.t, dtype=float).ravel()
    states = np.asarray(system.dae.ts.x, dtype=float)
    delta = states[:, np.asarray(model.delta.a).ravel().astype(int)]
    speed = states[:, np.asarray(model.omega.a).ravel().astype(int)]
    if delta.ndim == 1:
        delta = delta[:, None]
        speed = speed[:, None]
    return time, speed, delta


def _algebraic(system: Any, variable: Any) -> np.ndarray:
    values = np.asarray(system.dae.ts.y, dtype=float)[:, np.asarray(variable.a).ravel().astype(int)]
    if values.ndim == 1:
        values = values[:, None]
    return values


def _angle_error(system: Any, model_name: str) -> float:
    model = getattr(system, model_name)
    frequency = float(np.asarray(model.fn.v).ravel()[0])
    time, speed, delta = _machine_series(system, model_name)
    step = np.diff(time)[:, None]
    predicted = 2.0 * math.pi * frequency * 0.5 * ((speed[:-1] - 1.0) + (speed[1:] - 1.0)) * step
    return float(np.max(np.abs(np.diff(delta, axis=0) - predicted)))


def _pss_error(system: Any, time: np.ndarray, speed: np.ndarray, measured: np.ndarray) -> float:
    """IEEEST по скорости, когда второй фильтр и оба lead-lag имеют единичную передачу."""
    if not hasattr(system, "IEEEST") or system.IEEEST.n == 0:
        return 0.0
    block = system.IEEEST

    def value(name: str) -> float:
        return float(np.asarray(getattr(block, name).v).ravel()[0])

    if value("MODE") != 1.0 or value("A5") != value("A3") or value("A6") != value("A4"):
        return 1.0
    if value("T1") != value("T2") or value("T3") != value("T4"):
        return 1.0
    pole, inertia, wash_s, wash_gain, gain = value("A1"), value("A2"), value("T6"), value("T5"), value("KS")
    if inertia <= 0 or wash_s <= 0:
        return 1.0
    samples = np.concatenate([[1.0], np.asarray(speed, dtype=float)])
    moments = np.concatenate([[0.0], time])
    state = np.zeros(3)
    previous = _pss_deriv(state, 0.0, pole, inertia, wash_s, gain)
    replay = [0.0]
    for step, sample in zip(np.diff(moments), samples[1:], strict=True):
        deviation = float(sample - 1.0)

        def deriv_right(candidate: np.ndarray, deviation: float = deviation) -> np.ndarray:
            return _pss_deriv(candidate, deviation, pole, inertia, wash_s, gain)

        state = _implicit_trapezoid(state, float(step), previous, deriv_right)
        previous = deriv_right(state)
        replay.append(_pss_output(state, wash_s, wash_gain, gain))
    return float(np.max(np.abs(np.asarray(replay[1:]) - np.asarray(measured).ravel())))


def _pss_deriv(state: np.ndarray, deviation: float, pole: float, inertia: float, wash_s: float, gain: float) -> np.ndarray:
    lag_x, lag_y, wash = (float(item) for item in state)
    return np.array(
        [
            (deviation - lag_y - pole * lag_x) / inertia,
            lag_x,
            (gain * lag_y - wash) / wash_s,
        ]
    )


def _pss_output(state: np.ndarray, wash_s: float, wash_gain: float, gain: float) -> float:
    _lag_x, lag_y, wash = (float(item) for item in state)
    return (wash_gain / wash_s) * (gain * lag_y - wash)


def _governor_error(system: Any, time: np.ndarray, speed: np.ndarray) -> float:
    if system.TGOV1.n == 0:
        return 0.0
    droop = float(np.asarray(system.TGOV1.R.v).ravel()[0])
    power0 = float(np.asarray(system.GENROU.tm0.v).ravel()[0])
    samples = np.concatenate([[1.0], np.asarray(speed, dtype=float)])
    moments = np.concatenate([[0.0], time])
    replay = tgov1_power(
        samples,
        np.diff(moments),
        droop,
        float(np.asarray(system.TGOV1.T1.v).ravel()[0]),
        float(np.asarray(system.TGOV1.T2.v).ravel()[0]),
        float(np.asarray(system.TGOV1.T3.v).ravel()[0]),
        power0,
    )
    measured = _algebraic(system, system.TGOV1.pout)[:, 0]
    return float(np.max(np.abs(replay[1:] - measured)))

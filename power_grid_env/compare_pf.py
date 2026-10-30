"""Двухшинный режим против ANDES. Совпадение не является превосходством."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

VOLTAGE_LL_V = 110_000.0
POWER_BASE_VA = 100_000_000.0
RESISTANCE_PU = 0.01
REACTANCE_PU = 0.2
ACTIVE_PU = 0.3
REACTIVE_PU = 0.1


def qualify_two_bus() -> dict[str, Any]:
    """Модуль и угол напряжения нагрузки в ANDES и в фазной сети."""
    from power_grid_env.andes_rms import ensure_andes_importable

    if not ensure_andes_importable():
        raise ImportError("ANDES не установлен")
    import logging

    import andes

    logging.getLogger("andes").setLevel(logging.ERROR)
    other = _andes_voltage(andes)
    own = _network_voltage()
    reasons = []
    if not other["converged"] or own[1] != "converged":
        reasons.append("status")
    voltage_error = abs(other["voltage_pu"] - own[0])
    angle_error = abs(other["angle_rad"] - own[2])
    if voltage_error > 1e-5:
        reasons.append("voltage")
    if angle_error > 1e-4:
        reasons.append("angle")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error_pu": voltage_error,
        "angle_error_rad": angle_error,
        "andes_version": andes.__version__,
        "competitive_superiority_proven": False,
        "city_claim": False,
        "other_machine_install": False,
    }


def qualify_wheel() -> dict[str, Any]:
    """Колесо ставится в пустой каталог и импортируется. Это не другая машина."""
    root = Path(__file__).resolve().parents[1]
    wheel_dir = Path(tempfile.mkdtemp(prefix="pge-wheel-"))
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(wheel_dir), str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    wheels = sorted(wheel_dir.glob("power_grid_env-*.whl"))
    if built.returncode != 0 or not wheels:
        return {"verdict": "fail", "reasons": ["wheel"], "build_error": built.stderr[-4000:], "other_machine": False, "released": False}
    target = wheel_dir / "site"
    installed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheels[0])],
        check=False,
        capture_output=True,
        text=True,
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(target)
    smoke = """
import json
import power_grid_env
from power_grid_env.build import NetBuilder
from power_grid_env.backend_opendss import solve_network
b = NetBuilder('wheel', 'Wheel', 10., s_base_va=1e6)
b.node('src'); b.node('load'); b.source('src', .05, .2)
b.line_z1('src', 'load', .2+.4j, line_id='line')
b.load('load', 100000., 20000., load_id='load')
r = solve_network(b.build())
assert r.status == 'converged', r.status
print(json.dumps({'version': power_grid_env.__version__, 'path': power_grid_env.__file__, 'solve_status': r.status}))
"""
    probed = subprocess.run(
        [sys.executable, "-c", smoke],
        check=False,
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        env=env,
    )
    try:
        import json
        probe = json.loads(probed.stdout.strip())
    except (ValueError, TypeError):
        probe = {}
    version = probe.get("version")
    reasons = []
    if (installed.returncode != 0 or probed.returncode != 0 or version != "0.1.0"
            or not Path(probe.get("path", "/")).resolve().is_relative_to(target.resolve())):
        reasons.append("import")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "version": version,
        "solve_status": probe.get("solve_status"),
        "build_isolation": False,
        "fresh_dependency_environment": False,
        "other_machine": False,
        "released": False,
        "city_claim": False,
    }


def _andes_voltage(andes: Any) -> dict[str, float | bool]:
    from pathlib import Path
    import tempfile

    pycode = Path(tempfile.gettempdir()) / "power-grid-env-andes-pycode"
    pycode.mkdir(exist_ok=True)
    system = andes.System(no_output=True, default_config=True, pycode_path=str(pycode))
    system.config.freq = 50.0
    system.add("Bus", idx=1, Vn=110, v0=1.0)
    system.add("Bus", idx=2, Vn=110, v0=1.0)
    system.add("Slack", idx="SL", bus=1, Sn=100, Vn=110, v0=1.0, a0=0.0)
    system.add("PQ", idx="L", bus=2, Sn=100, Vn=110, p0=ACTIVE_PU, q0=REACTIVE_PU)
    system.add("Line", idx="LN", bus1=1, bus2=2, Sn=100, fn=50, Vn1=110, Vn2=110, r=RESISTANCE_PU, x=REACTANCE_PU, b=0.0)
    system.setup()
    converged = bool(system.PFlow.run())
    return {
        "converged": converged,
        "voltage_pu": float(np.asarray(system.Bus.v.v).ravel()[1]),
        "angle_rad": float(np.asarray(system.Bus.a.v).ravel()[1]),
    }


def _network_voltage() -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    impedance = VOLTAGE_LL_V**2 / POWER_BASE_VA
    builder = NetBuilder("cmp", "Сравнение", 110.0, s_base_va=POWER_BASE_VA)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1.0e-4, 1.0e-4)
    builder.line_z1("src", "ld", (RESISTANCE_PU + 1.0j * REACTANCE_PU) * impedance, line_id="line")
    builder.load("ld", ACTIVE_PU * POWER_BASE_VA, REACTIVE_PU * POWER_BASE_VA, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status, math.nan
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    base = VOLTAGE_LL_V / math.sqrt(3.0)
    return abs(voltage) / base, solved.status, math.atan2(voltage.imag, voltage.real)

"""Снимок готовности выпуска. Не ставит released, пока обязательные треки открыты."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from power_grid_env import __version__
from power_grid_env.capabilities import CAPABILITY_MATRIX
from power_grid_env.paths import package_dir
from power_grid_env.report import write_json

OPEN_LIMITS = (
    "AG-07: сутки и неделя двух фидеров SMART-DS через Engine не квалифицированы",
    "AG-10: горизонты 7/30 суток не измерены. Луч 150 кВт с 0.05+j0.08 Ом на участок не имеет решения PQ после 2054 шин; 10 000 шин с суммарным Z луча из 1000 сходятся",
    "AG-14: парных натурных измерений нет, трек waiting_for_data",
    "AG-15: RMS/ANDES не подключён",
    "сравнение с PyDSS, pandapower как независимый QSTS, Power Grid Model, Grid2Op и ANDES не выполнялось",
)


def write_qualification(out: Path) -> dict[str, Any]:
    files = {}
    for path in sorted(package_dir().glob("*.py")):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "version": __version__,
        "released": False,
        "competitive_superiority_proven": False,
        "validated_for_declared_scope": False,
        "open_limits": list(OPEN_LIMITS),
        "capabilities": CAPABILITY_MATRIX,
        "package_py_sha256": files,
        "supported_systems": ["Linux"],
    }
    write_json(out / "qualification.json", payload)
    return payload

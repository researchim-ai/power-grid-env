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
    "AG-10: 7 и 30 суток измерены на двухшинной схеме с шагом 1 сутки; 15-минутный фидер не измерен. Луч 150 кВт с 0.05+j0.08 Ом на участок не имеет решения PQ после 2054 шин; 10 000 шин с суммарным Z луча из 1000 сходятся",
    "AG-14: парных натурных измерений нет, трек waiting_for_data",
    "AG-15: схема Кундура в ANDES сверена со структурными схемами регуляторов. Промышленный сертификат RMS не выдан, произвольная сеть не заявлена",
    "двухшинное напряжение сверено с ANDES. PyDSS, Power Grid Model и Grid2Op не сравнивались. Установка на другой машине не проверялась",
)


def write_qualification(out: Path) -> dict[str, Any]:
    from power_grid_env.benchmarks import time_two_bus

    files = {}
    for path in sorted(package_dir().glob("*.py")):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    timing = time_two_bus(repeats=3)
    payload = {
        "version": __version__,
        "released": False,
        "competitive_superiority_proven": False,
        "validated_for_declared_scope": False,
        "open_limits": list(OPEN_LIMITS),
        "capabilities": CAPABILITY_MATRIX,
        "package_py_sha256": files,
        "supported_systems": ["Linux"],
        "measured_two_bus_p50_s": timing["p50_s"],
        "timing_is_a_city_claim": False,
    }
    write_json(out / "qualification.json", payload)
    return payload

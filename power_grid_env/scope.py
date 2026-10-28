"""Область расчёта. Параметры одного режима не подставляются в другой.

Гармонический спектр, инерция машины и нулевая последовательность источника
остаются пустыми, пока их не задали отдельно.
"""

from __future__ import annotations

from typing import Any, Literal

from power_grid_env.model import Network, parse_vector_group

StudyName = Literal["power_flow", "short_circuit", "rms", "harmonic"]


def compile_scope(network: Network, study: StudyName) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    blocked: list[dict[str, str]] = []
    supported: list[str] = []
    if study == "harmonic":
        if not network.harmonics:
            blocked.append(
                {
                    "id": network.id,
                    "reason": "спектр гармоник не задан и не заменён типовым спектром",
                }
            )
            errors.append("harmonic: нет частотной модели")
        else:
            for component in network.harmonics:
                supported.append(f"harmonic:{component.order}:{component.quality}")
                if component.quality == "declared":
                    warnings.append(f"порядок {component.order}: спектр объявлен, это не измерение")
                else:
                    warnings.append(
                        f"порядок {component.order}: пометка measured не делает расчёт натурно подтверждённым"
                    )
    for source in network.sources:
        supported.append(f"source:{source.id}:power_flow")
        if study == "short_circuit" and (source.r0_ohm is None or source.x0_ohm is None):
            errors.append(f"{source.id}: для КЗ нулевая последовательность не задана и не заменена нулём")
            blocked.append({"id": source.id, "reason": "нет r0/x0"})
    for line in network.lines:
        supported.append(f"line:{line.id}:power_flow")
        if (line.temperature_c is None) ^ (line.resistance_alpha_per_c is None):
            warnings.append(f"{line.id}: температура или коэффициент заданы порознь, R в режиме не пересчитывается")
        elif line.temperature_c is not None:
            warnings.append(f"{line.id}: R(T) записан в паспорте и в стационарном режиме не применяется")
    for transformer in network.transformers:
        try:
            parse_vector_group(transformer.vector_group)
        except ValueError as exc:
            errors.append(str(exc))
            blocked.append({"id": transformer.id, "reason": "группа не разобрана"})
        else:
            supported.append(f"transformer:{transformer.id}:power_flow")
    for generator in network.generators:
        supported.append(f"generator:{generator.id}:power_flow")
        if study == "rms" and (generator.inertia_s is None or generator.x_d_pu is None):
            errors.append(f"{generator.id}: инерция и x_d не взяты из P и Q режима")
            blocked.append({"id": generator.id, "reason": "нет отдельной RMS-модели"})
    for switch in network.switches:
        if switch.representation == "topological":
            warnings.append(f"{switch.id}: топологический аппарат, импеданс в матрицу проводимости не входит")
        supported.append(f"switch:{switch.id}:{switch.representation}")
    approximations = [
        f"{item.id}:{item.provenance.quality}"
        for item in [*network.lines, *network.transformers, *network.loads, *network.sources]
        if item.provenance.quality in {"assumed", "derived", "missing"}
    ]
    return {
        "study": study,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "supported": supported,
        "blocked": blocked,
        "approximations": approximations,
    }

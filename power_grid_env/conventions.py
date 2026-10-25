"""Единицы, знаки, базы и симметричные составляющие.

Внутренний API хранит фазоры как RMS-комплексные числа, углы в радианах,
мощность в ваттах и варах, напряжение — фаза-земля. Линейные напряжения,
градусы и кратные приставки допускаются только на границе ввода.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

PHASE_ORDER = ("A", "B", "C", "N")
PHASE_INDEX = {name: index for index, name in enumerate(("A", "B", "C"))}
A_OPERATOR = np.exp(1j * 2.0 * math.pi / 3.0)

# Численное представление «незаземлённой» нейтрали во внешнем backend.
# Это не молчаливый default оборудования: применяется только если модель явно
# задаёт grounding.kind = ungrounded, и всегда попадает в diagnostics.
UNGROUNDED_NEUTRAL_R_OHM = 1.0e8
# OpenDSS запрещает нулевой импеданс источника. Твёрдое соединение явной
# нейтрали с землёй через reactor получает это объявленное сопротивление.
SOLID_NEUTRAL_R_OHM = 1.0e-6

TO_SI = {
    "V": 1.0,
    "kV": 1.0e3,
    "MV": 1.0e6,
    "A": 1.0,
    "kA": 1.0e3,
    "W": 1.0,
    "kW": 1.0e3,
    "MW": 1.0e6,
    "var": 1.0,
    "kvar": 1.0e3,
    "Mvar": 1.0e6,
    "VA": 1.0,
    "kVA": 1.0e3,
    "MVA": 1.0e6,
    "ohm": 1.0,
    "S": 1.0,
    "mS": 1.0e-3,
    "Hz": 1.0,
    "s": 1.0,
    "min": 60.0,
    "h": 3600.0,
    "J": 1.0,
    "Wh": 3600.0,
    "kWh": 3.6e6,
    "MWh": 3.6e9,
    "rad": 1.0,
    "deg": math.pi / 180.0,
    "m": 1.0,
    "km": 1000.0,
    "ft": 0.3048,
    "mi": 1609.344,
}


def to_si(value: float, unit: str) -> float:
    try:
        factor = TO_SI[unit]
    except KeyError as exc:
        raise ValueError(f"неизвестная единица {unit!r}") from exc
    return float(value) * factor


def from_si(value_si: float, unit: str) -> float:
    try:
        factor = TO_SI[unit]
    except KeyError as exc:
        raise ValueError(f"неизвестная единица {unit!r}") from exc
    return float(value_si) / factor


def deg_to_rad(angle_deg: float) -> float:
    return float(angle_deg) * math.pi / 180.0


def rad_to_deg(angle_rad: float) -> float:
    return float(angle_rad) * 180.0 / math.pi


def ll_to_ln(voltage_ll: float) -> float:
    """Линейное RMS → фазное RMS для симметричной тройки."""
    return float(voltage_ll) / math.sqrt(3.0)


def ln_to_ll(voltage_ln: float) -> float:
    return float(voltage_ln) * math.sqrt(3.0)


def peak_to_rms(peak: float) -> float:
    return float(peak) / math.sqrt(2.0)


def rms_to_peak(rms: float) -> float:
    return float(rms) * math.sqrt(2.0)


def v_base_ln(voltage_ll: float) -> float:
    return ll_to_ln(voltage_ll)


def z_base(voltage_ll: float, s_base_va: float) -> float:
    """Z_base = V_ll^2 / S_3ph. Совпадает с пофазным V_ln^2 / S_phase."""
    if s_base_va <= 0 or voltage_ll <= 0:
        raise ValueError("базы напряжения и мощности должны быть положительными")
    return float(voltage_ll) ** 2 / float(s_base_va)


def i_base(voltage_ll: float, s_base_va: float) -> float:
    if s_base_va <= 0 or voltage_ll <= 0:
        raise ValueError("базы напряжения и мощности должны быть положительными")
    return float(s_base_va) / (math.sqrt(3.0) * float(voltage_ll))


def complex_power(voltage: complex, current_into_device: complex) -> complex:
    """S = V · conj(I).

    I — ток, входящий в оборудование из узла. Положительная P означает
    потребление из сети (потребительская конвенция). Генерация имеет
    отрицательную P на терминале источника.
    """
    return voltage * complex(current_into_device).conjugate()


def positive_sequence_phasor(magnitude_ln: float, angle_a_rad: float) -> np.ndarray:
    """Фазы A, B, C. B отстаёт от A на 120°, C опережает на 120°."""
    angles = (
        angle_a_rad,
        angle_a_rad - 2.0 * math.pi / 3.0,
        angle_a_rad + 2.0 * math.pi / 3.0,
    )
    return np.array(
        [magnitude_ln * np.exp(1j * angle) for angle in angles], dtype=np.complex128
    )


def fortescue_matrix() -> np.ndarray:
    """Матрица A такая, что V_abc = A @ V_012.

    a = exp(+j 2π/3). Прямая последовательность: [1, a², a].
    """
    a = A_OPERATOR
    return np.array(
        [
            [1, 1, 1],
            [1, a**2, a],
            [1, a, a**2],
        ],
        dtype=np.complex128,
    )


def to_sequence(phase_values: Iterable[complex]) -> np.ndarray:
    values = np.asarray(list(phase_values), dtype=np.complex128)
    if values.shape != (3,):
        raise ValueError("симметричные составляющие определены для трёх фаз A, B, C")
    return np.linalg.solve(fortescue_matrix(), values)


def from_sequence(sequence_values: Iterable[complex]) -> np.ndarray:
    values = np.asarray(list(sequence_values), dtype=np.complex128)
    if values.shape != (3,):
        raise ValueError("ожидаются составляющие 0, 1, 2")
    return fortescue_matrix() @ values


def symmetrical_components_applicable(matrix: np.ndarray, tol: float = 1e-9) -> bool:
    """Циркулянтная 3×3 матрица — необходимое условие применимости последовательностей."""
    z = np.asarray(matrix, dtype=np.complex128)
    if z.shape != (3, 3):
        return False
    diag = z[0, 0]
    off = z[0, 1]
    expected = np.array(
        [
            [diag, off, off],
            [off, diag, off],
            [off, off, diag],
        ],
        dtype=np.complex128,
    )
    scale = max(1.0, float(np.max(np.abs(z))))
    return bool(np.max(np.abs(z - expected)) <= tol * scale)


def sequence_impedances(matrix: np.ndarray) -> tuple[complex, complex, complex]:
    if not symmetrical_components_applicable(matrix):
        raise ValueError("матрица не циркулянтная: последовательности неприменимы")
    z = np.asarray(matrix, dtype=np.complex128)
    z_self = z[0, 0]
    z_mutual = z[0, 1]
    z1 = z_self - z_mutual
    z2 = z1
    z0 = z_self + 2.0 * z_mutual
    return z0, z1, z2


def align_angle(angle_rad: float, reference_rad: float) -> float:
    """Разность углов в (-π, π], после выбора опорного угла острова."""
    delta = angle_rad - reference_rad
    return math.atan2(math.sin(delta), math.cos(delta))

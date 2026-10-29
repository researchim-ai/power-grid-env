"""Поверхностный эффект круглого провода.

Две записи одной формулы: функции Кельвина и функция Бесселя комплексного
аргумента. Это не расчёт поля в пазу.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import bei, beip, ber, berp, jv


def slot_field_grid(depth_over_skin: float, points: int) -> np.ndarray:
    """Сетка уравнения d²H/dx² = γ² H в пазу. На дне поток не выходит, на поверхности H = 1."""
    if depth_over_skin <= 0 or points < 4:
        raise ValueError("глубина и число узлов сетки паза должны быть достаточными")
    step = 1.0 / points
    wave = ((1.0 + 1.0j) * depth_over_skin) ** 2 * step * step
    matrix = np.zeros((points, points), dtype=complex)
    rhs = np.zeros(points, dtype=complex)
    matrix[0, 0] = -2.0 - wave
    matrix[0, 1] = 2.0
    for row in range(1, points - 1):
        matrix[row, row - 1] = 1.0
        matrix[row, row] = -2.0 - wave
        matrix[row, row + 1] = 1.0
    matrix[points - 1, points - 2] = 1.0
    matrix[points - 1, points - 1] = -2.0 - wave
    rhs[points - 1] = -1.0
    return np.linalg.solve(matrix, rhs)


def slot_field_analytic(depth_over_skin: float, depth_fraction: float) -> complex:
    if depth_over_skin <= 0 or not 0.0 <= depth_fraction <= 1.0:
        raise ValueError("глубина паза и доля высоты должны быть согласованы")
    wave = (1.0 + 1.0j) * depth_over_skin
    return complex(np.cosh(wave * depth_fraction) / np.cosh(wave))


def slot_resistance_factor(depth_over_skin: float) -> float:
    """Коэффициент сопротивления прямоугольного стержня в пазу.

    ξ (sinh 2ξ + sin 2ξ) / (cosh 2ξ − cos 2ξ). При малом ξ стремится к 1.
    Это одномерный паз, не сеточный расчёт поля.
    """
    if depth_over_skin <= 0:
        raise ValueError("относительная глубина паза должна быть положительной")
    doubled = 2.0 * depth_over_skin
    return depth_over_skin * (math.sinh(doubled) + math.sin(doubled)) / (math.cosh(doubled) - math.cos(doubled))


def slot_resistance_coth(depth_over_skin: float) -> float:
    """Та же величина как действительная часть ξ(1+j) coth(ξ(1+j))."""
    if depth_over_skin <= 0:
        raise ValueError("относительная глубина паза должна быть положительной")
    wave = (1.0 + 1.0j) * depth_over_skin
    return float(np.real(wave / np.tanh(wave)))


def skin_factor_kelvin(argument: float) -> tuple[float, float]:
    """Rac/Rdc и Xвнутр/Rdc. argument = r √(ωμσ)."""
    if argument <= 0:
        raise ValueError("аргумент поверхностного эффекта должен быть положительным")
    denominator = berp(argument) ** 2 + beip(argument) ** 2
    resistance = (argument / 2.0) * (ber(argument) * beip(argument) - bei(argument) * berp(argument)) / denominator
    reactance = (argument / 2.0) * (ber(argument) * berp(argument) + bei(argument) * beip(argument)) / denominator
    return float(resistance), float(reactance)


def skin_factor_bessel(argument: float) -> float:
    """Rac/Rdc = Re[(ka/2) J0(ka)/J1(ka)], ka = m exp(−jπ/4)."""
    if argument <= 0:
        raise ValueError("аргумент поверхностного эффекта должен быть положительным")
    wave = argument * np.exp(-1j * math.pi / 4.0)
    value = (wave / 2.0) * jv(0, wave) / jv(1, wave)
    return float(np.real(value))

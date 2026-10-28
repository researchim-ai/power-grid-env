"""Независимые аналитические эталоны.

Этот модуль не является промышленным решателем и не вызывается из
испытательного контура. Он даёт замкнутые формулы и линейный узловой
анализ для постоянных сопротивлений, чтобы сравнивать внешний backend
с другой записью тех же уравнений.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from power_grid_env.conventions import ll_to_ln, positive_sequence_phasor


class NoHighVoltageRoot(RuntimeError):
    """Дискриминант двухшинной задачи PQ отрицателен: ветви режима нет."""


def receiving_voltage_pq(v_send: complex, z_series: complex, s_receive: complex) -> tuple[complex, complex]:
    """Две ветви напряжения приёмного конца.

    S — потребляемая мощность фазы, z — последовательный импеданс,
    v_send — фазное напряжение источника (за импедансом источника, если он
    уже включён в z). Возвращается (высокая ветвь, низкая ветвь).
    """
    if abs(z_series) == 0:
        if abs(s_receive) != 0 and abs(v_send) == 0:
            raise NoHighVoltageRoot("нулевой импеданс и ненулевая мощность")
        return v_send, v_send
    magnitude = abs(v_send)
    if magnitude == 0:
        raise NoHighVoltageRoot("нулевое напряжение передачи")
    angle = math.atan2(v_send.imag, v_send.real)
    resistance, reactance = z_series.real, z_series.imag
    active, reactive = s_receive.real, s_receive.imag
    y_imag = (resistance * reactive - reactance * active) / magnitude
    discriminant = magnitude**2 - 4.0 * (y_imag**2 + resistance * active + reactance * reactive)
    if discriminant < -1e-8:
        raise NoHighVoltageRoot("нет действительного напряжения приёмного конца")
    root = math.sqrt(max(0.0, discriminant))
    high = complex((magnitude + root) / 2.0, y_imag)
    low = complex((magnitude - root) / 2.0, y_imag)
    rotation = complex(math.cos(angle), math.sin(angle))
    return high * rotation, low * rotation


def zip_power(s_nominal: complex, voltage_pu: float, zp: float, ip: float, pp: float, zq: float, iq: float, pq: float) -> complex:
    """P = P0 (Zp v² + Ip v + Pp), аналогично Q. Коэффициенты уже нормированы на 1."""
    factor_p = zp * voltage_pu**2 + ip * voltage_pu + pp
    factor_q = zq * voltage_pu**2 + iq * voltage_pu + pq
    return complex(s_nominal.real * factor_p, s_nominal.imag * factor_q)


def constant_z_from_power(voltage_ln: complex, power: complex) -> complex:
    """Сопротивление, потребляющее заданную S при заданном фазном напряжении."""
    if power == 0:
        return complex(math.inf, 0.0)
    return voltage_ln * voltage_ln.conjugate() / power.conjugate() if False else (abs(voltage_ln) ** 2) / power.conjugate()


@dataclass
class MNAResult:
    voltages: dict[tuple[str, str], complex]
    current_into_source: dict[str, complex]


@dataclass
class MNA:
    """Модифицированные узловые уравнения для линейной схемы постоянных Z."""

    frequency_hz: float
    voltages_internal: dict[tuple[str, str], complex] = field(default_factory=dict)
    _y: dict[tuple[tuple[str, str], tuple[str, str]], complex] = field(default_factory=dict)
    _i: dict[tuple[str, str], complex] = field(default_factory=dict)

    def stamp_series(self, left: list[tuple[str, str]], right: list[tuple[str, str]], z_matrix: np.ndarray) -> None:
        y_matrix = np.linalg.inv(np.asarray(z_matrix, dtype=np.complex128))
        self._stamp_block(left, left, y_matrix)
        self._stamp_block(right, right, y_matrix)
        self._stamp_block(left, right, -y_matrix)
        self._stamp_block(right, left, -y_matrix)

    def stamp_shunt(self, nodes: list[tuple[str, str]], y_matrix: np.ndarray) -> None:
        self._stamp_block(nodes, nodes, np.asarray(y_matrix, dtype=np.complex128))

    def stamp_admittance_to_ground(self, node: tuple[str, str], admittance: complex) -> None:
        self._y[(node, node)] = self._y.get((node, node), 0j) + admittance

    def stamp_norton(self, node: tuple[str, str], y_to_ground: complex, current_injection: complex) -> None:
        self.stamp_admittance_to_ground(node, y_to_ground)
        self._i[node] = self._i.get(node, 0j) + current_injection

    def stamp_wye_transformer(
        self,
        hv: tuple[str, str],
        lv: tuple[str, str],
        z_hv_ohm: complex,
        turns_hv_over_lv: float,
    ) -> None:
        """Классический штамп двухобмоточного Yg–Yg на одну фазу.

        z_hv_ohm — сопротивление КЗ, приведённое к стороне ВН.
        turns_hv_over_lv = (Vhv / Vlv) * (tap_hv / tap_lv).
        """
        if turns_hv_over_lv == 0:
            raise ValueError("нулевой коэффициент трансформации")
        admittance = 1.0 / z_hv_ohm
        ratio = turns_hv_over_lv
        self._stamp_block([hv], [hv], np.array([[admittance]], dtype=np.complex128))
        self._stamp_block([lv], [lv], np.array([[admittance / ratio**2]], dtype=np.complex128))
        coupling = np.array([[-admittance / ratio]], dtype=np.complex128)
        self._stamp_block([hv], [lv], coupling)
        self._stamp_block([lv], [hv], coupling)

    def _stamp_block(self, rows: list[tuple[str, str]], cols: list[tuple[str, str]], block: np.ndarray) -> None:
        for i, row in enumerate(rows):
            for j, col in enumerate(cols):
                value = complex(block[i, j])
                self._y[(row, col)] = self._y.get((row, col), 0j) + value

    def solve(self) -> MNAResult:
        nodes = sorted({node for pair in self._y for node in pair} | set(self._i))
        index = {node: position for position, node in enumerate(nodes)}
        size = len(nodes)
        matrix = np.zeros((size, size), dtype=np.complex128)
        rhs = np.zeros(size, dtype=np.complex128)
        for (left, right), value in self._y.items():
            matrix[index[left], index[right]] += value
        for node, current in self._i.items():
            rhs[index[node]] += current
        try:
            solution = np.linalg.solve(matrix, rhs)
        except np.linalg.LinAlgError as exc:
            raise RuntimeError("узловая матрица вырождена: остров без опоры или обрыв фазы") from exc
        voltages = {node: complex(solution[index[node]]) for node in nodes}
        return MNAResult(voltages=voltages, current_into_source={})


def balanced_line_matrix(z1: complex, z_mutual: complex, size: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Циркулянтная матрица: Z1 = Zсобств − Zвзаим."""
    z_self = z1 + z_mutual
    matrix = np.full((size, size), z_mutual, dtype=np.complex128)
    np.fill_diagonal(matrix, z_self)
    return np.real(matrix).astype(float), np.imag(matrix).astype(float)


def source_phase_voltage(ll_v: float, angle_a_rad: float = 0.0) -> np.ndarray:
    return positive_sequence_phasor(ll_to_ln(ll_v), angle_a_rad)


def thevenin_fault_current(
    v_ln: complex,
    z1: complex,
    z2: complex,
    z0: complex,
    z_fault: complex,
    kind: str,
) -> complex:
    """Ток КЗ по последовательности для источника за Z1=Z2, Z0.

    Возвращается фазный ток повреждённой фазы A для несимметричных видов
    и фазный ток для трёхфазного КЗ. Классическая машина здесь не используется.
    """
    if kind == "3ph":
        return v_ln / (z1 + z_fault)
    if kind == "slg":
        return 3.0 * v_ln / (z1 + z2 + z0 + 3.0 * z_fault)
    if kind == "ll":
        # Междуфазный ток в фазе A при КЗ BC равен нулю в симметричной постановке,
        # если повреждены B и C. Для КЗ AB ток фазы A:
        return v_ln / (z1 + z2 + z_fault) * (1 - np.exp(-1j * 2 * math.pi / 3)) / (1) * 0 + (
            (v_ln - v_ln * np.exp(-1j * 2 * math.pi / 3)) / (z1 + z2 + z_fault)
        )
    if kind == "llg":
        z_parallel = (z2 * (z0 + 3 * z_fault)) / (z2 + z0 + 3 * z_fault)
        i1 = v_ln / (z1 + z_parallel)
        return 3 * i1  # ток в землю, не фазный; вызывающий код различает kind
    raise ValueError(f"неизвестный вид КЗ {kind}")


def line_to_line_current(v_ln: complex, z1: complex, z2: complex, z_fault: complex) -> complex:
    """Ток между фазами A и B. Напряжение AB = V∠0 − V∠−120."""
    v_ab = v_ln - v_ln * np.exp(-1j * 2.0 * math.pi / 3.0)
    return v_ab / (z1 + z2 + z_fault)


def llg_phase_currents(
    v_ln: complex,
    z1: complex,
    z2: complex,
    z0: complex,
    z_fault: complex,
) -> tuple[complex, complex, complex]:
    """Двухфазное КЗ на землю фаз B и C. В каждой повреждённой фазе свой Zf на землю.

    Фазная матрица источника: Zсобств = (Z0+2 Z1)/3, Zвзаим = (Z0−Z1)/3 при Z1=Z2.
    Фаза A разомкнута. Это не параллель Z2 и (Z0+3 Zf) и не IEC 60909.
    """
    if z1 != z2:
        raise ValueError("эта запись матрицы требует Z1 = Z2")
    self_z = (z0 + 2.0 * z1) / 3.0
    mutual = (z0 - z1) / 3.0
    operator = np.exp(1j * 2.0 * math.pi / 3.0)
    emf_b = v_ln * operator**2
    emf_c = v_ln * operator
    matrix = np.array([[self_z + z_fault, mutual], [mutual, self_z + z_fault]], dtype=np.complex128)
    phase_b, phase_c = np.linalg.solve(matrix, np.array([emf_b, emf_c], dtype=np.complex128))
    return 0j, complex(phase_b), complex(phase_c)

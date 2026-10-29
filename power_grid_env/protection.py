"""Защиты квазистационарного шага.

Характеристики IEC 60255-151:2009, таблица A.1. Дистанционная зона ниже —
сравнение Z=V/I с лучом, без фильтра переходного режима.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

IEC_CURVES = {
    "standard_inverse": (0.14, 0.02),
    "very_inverse": (13.5, 1.0),
    "extremely_inverse": (80.0, 2.0),
}


def iec_operate_time(current_a: float, pickup_a: float, curve: str, time_multiplier: float) -> float:
    if current_a <= pickup_a:
        return math.inf
    if curve == "definite":
        return time_multiplier
    try:
        k_factor, alpha = IEC_CURVES[curve]
    except KeyError as exc:
        raise ValueError(
            f"кривая {curve} не входит в реализованный набор IEC 60255-151 таблица A.1"
        ) from exc
    ratio = current_a / pickup_a
    return time_multiplier * k_factor / (ratio**alpha - 1.0)


def directional_allows(voltage: complex, current: complex, forward: bool = True) -> bool:
    """Грубая направленность: знак активной мощности в фазе напряжения.

    forward=True пропускает ток, отдающий положительную P из узла в линию,
    то есть Re(V · conj(I)) > 0, где I входит в контролируемый элемент.
    """
    if abs(voltage) < 1e-6 or abs(current) < 1e-9:
        return False
    power = (voltage * current.conjugate()).real
    return power > 0 if forward else power < 0


@dataclass
class RelayState:
    relay_id: str
    kind: Literal["overcurrent", "undervoltage", "overvoltage"]
    equipment_id: str
    pickup: float
    curve: str = "definite"
    time_multiplier_s: float = 1.0
    breaker_delay_s: float = 0.0
    reset_s: float = 1.0
    accumulator_s: float = 0.0
    timing: bool = False
    tripped: bool = False
    shots: int = 0
    max_shots: int | None = None
    reclose_delay_s: float | None = None
    lockout: bool = False
    pending_close_at: float | None = None
    directional: bool = False
    measured_equipment_id: str | None = None
    measured_node_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def integrate(self, signal: float, dt_s: float, voltage: complex | None = None, current: complex | None = None) -> str | None:
        if self.directional and voltage is not None and current is not None:
            if not directional_allows(voltage, current, forward=True):
                signal = 0.0
        return advance_relay(self, signal, dt_s)


def sync_allows(
    left: complex,
    right: complex,
    left_base_v: float,
    right_base_v: float,
    *,
    max_angle_rad: float,
    max_voltage_pu: float,
    dead_pu: float,
) -> tuple[bool, str]:
    """Включение на погашенную сторону разрешено. Две живые стороны сверяются по модулю и углу."""
    if left_base_v <= 0 or right_base_v <= 0 or max_angle_rad <= 0 or max_voltage_pu <= 0 or dead_pu <= 0:
        raise ValueError("базы и пределы синхронизма должны быть положительными")
    left_pu = abs(left) / left_base_v
    right_pu = abs(right) / right_base_v
    if left_pu < dead_pu or right_pu < dead_pu:
        return True, "dead_bus"
    if abs(left_pu - right_pu) > max_voltage_pu:
        return False, "voltage_difference"
    difference = math.atan2(math.sin(cmath_angle(left) - cmath_angle(right)), math.cos(cmath_angle(left) - cmath_angle(right)))
    if abs(difference) > max_angle_rad:
        return False, "angle_difference"
    return True, "synced"


def ats_decision(
    primary: complex,
    reserve: complex,
    primary_base_v: float,
    reserve_base_v: float,
    *,
    primary_closed: bool,
    reserve_closed: bool,
    reserve_available: bool,
    max_angle_rad: float,
    max_voltage_pu: float,
    dead_pu: float,
) -> dict[str, bool | str]:
    """АВР по двум напряжениям.

    Погашенный рабочий ввод и живой резерв дают команду переключения.
    Резерв без измерения или сам погашенный остаётся разомкнутым.
    Две живые стороны остаются параллельными только если синхронизм проходит,
    иначе резерв размыкается. Команду задают напряжения: предыдущее положение
    рабочего выключателя само по себе ввод не переключает.
    """
    del primary_closed
    if primary_base_v <= 0 or reserve_base_v <= 0 or dead_pu <= 0:
        raise ValueError("базы и порог погашенной шины АВР должны быть положительными")
    primary_dead = abs(primary) / primary_base_v < dead_pu
    reserve_dead = abs(reserve) / reserve_base_v < dead_pu
    if primary_dead:
        if not reserve_available:
            return {"mode": "blocked", "primary_closed": False, "reserve_closed": False, "reason": "reserve_unavailable"}
        if reserve_dead:
            return {"mode": "blocked", "primary_closed": False, "reserve_closed": False, "reason": "reserve_dead"}
        allowed, reason = sync_allows(
            primary,
            reserve,
            primary_base_v,
            reserve_base_v,
            max_angle_rad=max_angle_rad,
            max_voltage_pu=max_voltage_pu,
            dead_pu=dead_pu,
        )
        if not allowed:
            return {"mode": "blocked", "primary_closed": False, "reserve_closed": False, "reason": reason}
        return {"mode": "transferred", "primary_closed": False, "reserve_closed": True, "reason": reason}
    if reserve_closed:
        allowed, reason = sync_allows(
            primary,
            reserve,
            primary_base_v,
            reserve_base_v,
            max_angle_rad=max_angle_rad,
            max_voltage_pu=max_voltage_pu,
            dead_pu=dead_pu,
        )
        if not allowed:
            return {"mode": "blocked", "primary_closed": True, "reserve_closed": False, "reason": reason}
        return {"mode": "parallel", "primary_closed": True, "reserve_closed": True, "reason": reason}
    return {"mode": "normal", "primary_closed": True, "reserve_closed": False, "reason": "primary_live"}


def cmath_angle(value: complex) -> float:
    return math.atan2(value.imag, value.real)


def swing_block(
    previous: complex,
    current: complex,
    dt_s: float,
    *,
    minimum_ohm_per_s: float,
    in_zone: bool,
    available: bool = True,
) -> dict[str, bool | float | str]:
    """Медленный вход в зону не отключает. Скачок импеданса считается КЗ.

    Это правило по двум фазорам, не фильтр переходного режима.
    """
    if dt_s <= 0 or minimum_ohm_per_s <= 0:
        raise ValueError("шаг и порог скорости импеданса должны быть положительными")
    if not available:
        return {"trip": False, "reason": "measurement_unavailable", "speed_ohm_per_s": 0.0, "dynamic_qualified": False}
    speed = abs(current - previous) / dt_s
    if not in_zone:
        return {"trip": False, "reason": "outside", "speed_ohm_per_s": speed, "dynamic_qualified": False}
    if speed < minimum_ohm_per_s:
        return {"trip": False, "reason": "swing", "speed_ohm_per_s": speed, "dynamic_qualified": False}
    return {"trip": True, "reason": "fault", "speed_ohm_per_s": speed, "dynamic_qualified": False}


def harmonic_restraint(
    operate_a: float,
    fundamental_a: float,
    second_a: float,
    *,
    pickup_a: float,
    second_ratio: float,
    available: bool = True,
) -> dict[str, bool | float | str]:
    """Вторая гармоника выше доли основной блокирует дифференциал. Это не фильтр КЗ."""
    if pickup_a < 0 or second_ratio <= 0 or fundamental_a < 0 or second_a < 0 or operate_a < 0:
        raise ValueError("токи и пороги торможения должны быть неотрицательными, доля — положительной")
    if not available or fundamental_a == 0.0:
        return {"trip": False, "reason": "measurement_unavailable", "ratio": 0.0, "dynamic_qualified": False}
    ratio = second_a / fundamental_a
    if ratio > second_ratio:
        return {"trip": False, "reason": "second_harmonic", "ratio": ratio, "dynamic_qualified": False}
    if operate_a > pickup_a:
        return {"trip": True, "reason": "operate", "ratio": ratio, "dynamic_qualified": False}
    return {"trip": False, "reason": "below_pickup", "ratio": ratio, "dynamic_qualified": False}


def distance_zones(
    voltage: complex,
    current: complex,
    *,
    zone1_ohm: float,
    zone2_ohm: float,
    line_angle_rad: float,
    half_width_rad: float,
    zone1_s: float,
    zone2_s: float,
    available: bool = True,
) -> dict[str, float | bool | str]:
    """Две зоны луча. Первая быстрее второй. Это не фильтр переходного режима."""
    if zone2_ohm <= zone1_ohm or zone1_s < 0 or zone2_s <= zone1_s:
        raise ValueError("вторая зона должна быть дальше и медленнее первой")
    near = distance_zone(
        voltage, current, reach_ohm=zone1_ohm, line_angle_rad=line_angle_rad, half_width_rad=half_width_rad, available=available
    )
    far = distance_zone(
        voltage, current, reach_ohm=zone2_ohm, line_angle_rad=line_angle_rad, half_width_rad=half_width_rad, available=available
    )
    if near["trip"]:
        zone, delay = "zone1", zone1_s
    elif far["trip"]:
        zone, delay = "zone2", zone2_s
    else:
        zone, delay = "none", math.inf
    return {"zone": zone, "delay_s": delay, "trip": zone != "none", "dynamic_qualified": False}


def reconnect_allowed(inside: list[bool], step_s: float, restore_s: float) -> bool:
    """Возврат после отключения: окно должно держаться непрерывно заданное время."""
    if step_s <= 0 or restore_s <= 0:
        raise ValueError("шаг и выдержка возврата должны быть положительными")
    needed = int(math.ceil(restore_s / step_s - 1e-12))
    if len(inside) < needed:
        return False
    return all(inside[-needed:])


def distance_zone(
    voltage: complex,
    current: complex,
    *,
    reach_ohm: float,
    line_angle_rad: float,
    half_width_rad: float,
    available: bool = True,
) -> dict[str, float | bool | str]:
    """Луч |Z| и угол линии. dynamic_qualified всегда ложен: это не фильтр КЗ."""
    if reach_ohm <= 0 or half_width_rad <= 0:
        raise ValueError("луч и ширина угла дистанционной зоны должны быть положительными")
    if not available or abs(current) < 1e-6:
        return {"trip": False, "reason": "measurement_unavailable", "impedance_ohm": 0.0, "dynamic_qualified": False}
    impedance = voltage / current
    angle = math.atan2(impedance.imag, impedance.real)
    difference = math.atan2(math.sin(angle - line_angle_rad), math.cos(angle - line_angle_rad))
    inside = abs(impedance) <= reach_ohm and abs(difference) <= half_width_rad
    return {
        "trip": inside,
        "reason": "mho" if inside else "outside",
        "impedance_ohm": abs(impedance),
        "dynamic_qualified": False,
    }


def breaker_can_interrupt(peak_a: float, interrupt_a: float, *, available: bool = True) -> dict[str, bool | str | float]:
    """Пик сравнивается с током отключения. Нет номинала — нет разрешения на отключение."""
    if peak_a < 0 or interrupt_a <= 0:
        raise ValueError("пик неотрицателен, ток отключения положителен")
    if not available:
        return {"can_interrupt": False, "reason": "rating_unavailable", "margin_a": 0.0}
    margin = interrupt_a - peak_a
    if margin >= 0.0:
        return {"can_interrupt": True, "reason": "within_rating", "margin_a": margin}
    return {"can_interrupt": False, "reason": "exceeds_rating", "margin_a": margin}


def instrument_secondary(primary: float, primary_rated: float, secondary_rated: float) -> float:
    """Вторичная величина. Номиналы CT или VT не подставляются из тока аварии."""
    if primary_rated <= 0 or secondary_rated <= 0:
        raise ValueError("номинал измерительного трансформатора должен быть положительным")
    return primary * secondary_rated / primary_rated


def trip_from_secondary(
    primary: float,
    primary_rated: float,
    secondary_rated: float,
    pickup_secondary: float,
    *,
    available: bool,
) -> dict[str, bool | float | str | None]:
    """Реле сравнивает вторичную величину. Нет измерения — нет отключения."""
    if not available:
        return {"trip": False, "reason": "measurement_unavailable", "secondary": None}
    secondary = instrument_secondary(primary, primary_rated, secondary_rated)
    return {"trip": secondary > pickup_secondary, "reason": "threshold", "secondary": secondary}


def transformer_differential(
    current_hv: complex,
    current_lv: complex,
    turns_ratio: float,
    *,
    slope: float,
    pickup_a: float,
    available: bool = True,
) -> dict[str, float | bool | str]:
    """Сравнение токов обмоток, приведённых к низкой стороне.

    Токи входят в трансформатор. Сквозной ток даёт почти нуль, внутреннее
    повреждение — сумму. Это фазорное торможение, не фильтр переходного режима.
    """
    if turns_ratio <= 0 or slope < 0 or pickup_a < 0:
        raise ValueError("коэффициент, наклон и уставка дифференциальной защиты должны быть неотрицательными")
    if not available:
        return {
            "trip": False,
            "reason": "measurement_unavailable",
            "operate_a": 0.0,
            "restraint_a": 0.0,
            "dynamic_qualified": False,
        }
    operate = current_hv * turns_ratio + current_lv
    restraint = 0.5 * (abs(current_hv) * turns_ratio + abs(current_lv))
    trip = abs(operate) > pickup_a and abs(operate) > slope * restraint
    return {
        "trip": trip,
        "reason": "restraint",
        "operate_a": abs(operate),
        "restraint_a": restraint,
        "dynamic_qualified": False,
    }


def reclose_ready(elapsed_s: float, dead_s: float) -> bool:
    """Повторное включение разрешено только после бестоковой паузы."""
    if dead_s <= 0 or elapsed_s < 0:
        raise ValueError("пауза АПВ должна быть положительной, время с отключения — неотрицательным")
    return elapsed_s + 1e-12 >= dead_s


def breaker_failure_time(primary_s: float, fail_delay_s: float, upstream_s: float, *, failed: bool) -> float:
    """Время снятия КЗ. Если выключатель отказал, резерв — раньше из таймера отказа и верхнего реле."""
    if primary_s < 0 or fail_delay_s <= 0 or upstream_s < 0:
        raise ValueError("времена защиты должны быть согласованы, задержка отказа — положительной")
    if math.isinf(primary_s):
        return math.inf
    if not failed:
        return primary_s
    return min(primary_s + fail_delay_s, upstream_s)


def first_to_clear(current_a: float, relay_s: float, fuse_a2s: float) -> str:
    """Кто достигает своего интеграла раньше при постоянном действующем токе.

    Накопленное i²t за время реле равно I² t. Плавкая вставка плавится, когда
    это число достигает номинала. Ниже уставки реле не участвует: relay_s = inf.
    """
    if current_a <= 0 or relay_s < 0 or fuse_a2s <= 0:
        raise ValueError("ток, время реле и номинал вставки должны быть согласованы")
    fuse_s = fuse_a2s / (current_a * current_a)
    if math.isinf(relay_s) or fuse_s < relay_s:
        return "fuse"
    if fuse_s > relay_s:
        return "relay"
    return "same"


def rocof_hz_per_s(samples: list[tuple[float, float]]) -> float:
    """Наклон частоты по окну. samples — пары (время, Гц) в порядке времени."""
    if len(samples) < 2:
        raise ValueError("для скорости изменения частоты нужны два отсчёта")
    start_t, start_f = samples[0]
    end_t, end_f = samples[-1]
    if end_t <= start_t:
        raise ValueError("окно частоты должно идти вперёд по времени")
    for earlier, later in zip(samples, samples[1:]):
        if later[0] <= earlier[0]:
            raise ValueError("отсчёты частоты должны возрастать по времени")
    return (end_f - start_f) / (end_t - start_t)


def rocof_trip(
    samples: list[tuple[float, float]],
    threshold_hz_per_s: float,
    *,
    available: bool = True,
) -> dict[str, bool | float | str]:
    """Отключение, если частота падает быстрее порога. Роста и отказа измерения недостаточно."""
    if threshold_hz_per_s <= 0:
        raise ValueError("порог ROCOF должен быть положительным")
    if not available or len(samples) < 2:
        return {"trip": False, "reason": "measurement_unavailable", "slope_hz_per_s": 0.0}
    slope = rocof_hz_per_s(samples)
    if slope <= -threshold_hz_per_s:
        return {"trip": True, "reason": "falling", "slope_hz_per_s": slope}
    return {"trip": False, "reason": "inside", "slope_hz_per_s": slope}


def earth_directional(
    voltage: complex,
    residual: complex,
    *,
    pickup_a: float,
    available: bool = True,
) -> dict[str, bool | float | str]:
    """Земля по сумме фаз. Нет измерения или ток ниже уставки — нет отключения.

    Направление — то же правило знака мощности, что у фазного реле.
    """
    if pickup_a <= 0:
        raise ValueError("уставка земляной защиты должна быть положительной")
    if not available:
        return {"trip": False, "reason": "measurement_unavailable", "residual_a": 0.0}
    magnitude = abs(residual)
    if magnitude <= pickup_a:
        return {"trip": False, "reason": "below_pickup", "residual_a": magnitude}
    if not directional_allows(voltage, residual, forward=True):
        return {"trip": False, "reason": "reverse", "residual_a": magnitude}
    return {"trip": True, "reason": "forward", "residual_a": magnitude}


def undervoltage_shed(
    voltage_pu: float,
    stages: list[tuple[float, list[str]]],
    critical_ids: set[str],
    *,
    available: bool = True,
) -> list[str]:
    """Ступени ниже порога напряжения. Критическая нагрузка остаётся. Нет измерения — ничего не снимается."""
    if not available:
        return []
    shed: list[str] = []
    for threshold_pu, load_ids in stages:
        if threshold_pu <= 0:
            raise ValueError("порог разгрузки по напряжению должен быть положительным")
        if voltage_pu < threshold_pu:
            for load_id in load_ids:
                if load_id not in critical_ids and load_id not in shed:
                    shed.append(load_id)
    return shed


def underfrequency_shed(
    frequency_hz: float,
    stages: list[tuple[float, list[str]]],
    critical_ids: set[str],
    *,
    available: bool = True,
) -> list[str]:
    """Ступени ниже порога частоты. Критическая нагрузка остаётся. Нет измерения — ничего не снимается."""
    if not available:
        return []
    shed: list[str] = []
    for threshold_hz, load_ids in stages:
        if threshold_hz <= 0:
            raise ValueError("порог частоты разгрузки должен быть положительным")
        if frequency_hz < threshold_hz:
            for load_id in load_ids:
                if load_id not in critical_ids and load_id not in shed:
                    shed.append(load_id)
    return shed


def residual_current(phase_currents: list[complex]) -> complex:
    """3I0 для земли. Сумма симметричных фаз равна нулю."""
    if len(phase_currents) != 3:
        raise ValueError("земля считается по трём фазным токам")
    return phase_currents[0] + phase_currents[1] + phase_currents[2]


def critical_stays_supplied(cleared_breaker: str, lost_buses: dict[str, set[str]], critical_bus: str) -> bool:
    """Критическая шина остаётся, если отключённый аппарат не питает её сверху."""
    if cleared_breaker not in lost_buses:
        raise ValueError("аппарат не входит в карту питания")
    return critical_bus not in lost_buses[cleared_breaker]


def clearing_times(
    current_a: float,
    stages: list[tuple[str, float, str, float]],
) -> list[tuple[str, float]]:
    """Пары (id, уставка, кривая, TMS). Время inf, если ток не выше уставки.

    Это карта селективности по току, не дистанционная и не дифференциальная защита.
    """
    ordered = []
    for relay_id, pickup_a, curve, multiplier in stages:
        ordered.append((relay_id, iec_operate_time(current_a, pickup_a, curve, multiplier)))
    return sorted(ordered, key=lambda item: item[1])


def linear_reset(accumulator_s: float, dt_s: float, reset_s: float) -> float:
    """Линейный сброс аккумулятора за reset_s. Это не кривая возврата IEC."""
    if reset_s <= 0:
        return 0.0
    return max(0.0, accumulator_s - dt_s / reset_s)


def advance_relay(state: RelayState, signal: float, dt_s: float) -> str | None:
    """Интегратор без двусмысленного сброса: ниже уставки аккумулятор спадает линейно."""
    if state.lockout:
        return None
    if state.kind == "overcurrent":
        active = signal > state.pickup
        operate = iec_operate_time(signal, state.pickup, state.curve, state.time_multiplier_s) if active else math.inf
    elif state.kind == "undervoltage":
        active = signal < state.pickup
        operate = state.time_multiplier_s
    else:
        active = signal > state.pickup
        operate = state.time_multiplier_s
    if not active:
        if state.reset_s <= 0:
            state.accumulator_s = 0.0
        else:
            span = operate if state.kind == "overcurrent" and state.curve != "definite" else state.time_multiplier_s
            # Линейный сброс полной выдержки за reset_s.
            full = 1.0 if state.kind == "overcurrent" and state.curve != "definite" else state.time_multiplier_s
            state.accumulator_s = max(0.0, state.accumulator_s - full * dt_s / state.reset_s)
        state.timing = False
        return None
    if state.kind == "overcurrent" and state.curve != "definite":
        state.accumulator_s += dt_s / operate
        state.timing = True
        if state.accumulator_s >= 1.0:
            state.accumulator_s = 0.0
            state.timing = False
            state.tripped = True
            state.shots += 1
            if state.max_shots is not None and state.shots >= state.max_shots:
                state.lockout = True
            return "trip"
        return None
    state.accumulator_s += dt_s
    state.timing = True
    if state.accumulator_s + 1e-12 >= state.time_multiplier_s + state.breaker_delay_s:
        state.accumulator_s = 0.0
        state.timing = False
        state.tripped = True
        state.shots += 1
        if state.max_shots is not None and state.shots >= state.max_shots:
            state.lockout = True
        return "trip"
    return None

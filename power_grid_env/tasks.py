"""Каталог задач для алгоритмов. Обучение здесь не запускается.

Каждая задача называет наблюдаемые входы, действия и базовую политику.
Сравнение с удержанием недостаточно, поэтому рядом указана детерминированная эвристика.
"""

from __future__ import annotations

TASKS = [
    {
        "id": "restoration",
        "observables": ["switch_closed", "voltage_pu", "measurement_age_s", "action_mask"],
        "actions": ["hold", "switch"],
        "baselines": ["hold", "heuristic"],
        "hidden": ["fault_r_ohm", "future_profile"],
    },
    {
        "id": "volt_var",
        "observables": ["voltage_pu", "inverter_q"],
        "actions": ["setpoint_var"],
        "baselines": ["hold", "volt_var_curve"],
        "hidden": ["study_faults"],
    },
    {
        "id": "bess_dispatch",
        "observables": ["soc", "load_p", "price_if_supplied"],
        "actions": ["setpoint_w"],
        "baselines": ["hold", "soc_band"],
        "hidden": ["future_price"],
    },
    {
        "id": "contingency_screening",
        "observables": ["n1_status", "overload"],
        "actions": ["rank"],
        "baselines": ["full_n1"],
        "hidden": ["operator_intent"],
    },
    {
        "id": "diagnosis",
        "observables": ["voltages", "alarms"],
        "actions": ["hypothesize_fault"],
        "baselines": ["known_section"],
        "hidden": ["scenario_truth"],
    },
]


def task_ids() -> list[str]:
    return [item["id"] for item in TASKS]

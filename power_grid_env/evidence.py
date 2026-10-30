"""Реестр доказательств. Исторический расчёт не открывает профиль поставки.

Состояния не смешиваются: регрессия примера, независимая сверка, допуск профиля
и натурное подтверждение записываются раздельно.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from power_grid_env.paths import package_dir

EVIDENCE_STATES = (
    "implemented",
    "regression_verified",
    "independently_validated",
    "qualified_for_profile",
    "field_validated",
)

_PROFILES: dict[str, tuple[dict[str, Any], ...]] = {
    "industrial-network-studies": (
        {
            "id": "plant_pack",
            "network_class": "industrial_plant_110_10_0_4",
            "state": "qualified_for_profile",
            "metric": "voltage_error_pu",
            "tolerance": 1e-5,
        },
        {
            "id": "two_feeder_qsts",
            "network_class": "two_feeders_15min",
            "state": "qualified_for_profile",
            "metric": "missing_intervals",
            "tolerance": 0.0,
        },
        {
            "id": "clean_install",
            "network_class": "other_machine",
            "state": "qualified_for_profile",
            "metric": "install_gap",
            "tolerance": 0.0,
        },
    ),
    "industrial-dynamic-studies": (
        {
            "id": "imported_rms",
            "network_class": "imported_plant",
            "state": "qualified_for_profile",
            "metric": "trajectory_gap",
            "tolerance": 0.0,
        },
    ),
    "advanced-studies": (
        {
            "id": "network_harmonics",
            "network_class": "industrial_plant_110_10_0_4",
            "state": "qualified_for_profile",
            "metric": "spectrum_error",
            "tolerance": 1e-4,
        },
    ),
}


def package_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir().glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def historical_claims() -> list[dict[str, Any]]:
    """PM-00…11 остаются регрессиями своих схем. Поле и профиль поставки не открыты."""
    scopes = {
        "PM-00": "якобиан трапеции и отказ несходящегося шага на объявленных примерах",
        "PM-01": "паспорта части оборудования, не полная матрица площадки",
        "PM-02": "фазные эталоны объявленных схем, не произвольный импорт",
        "PM-03": "7 и 30 суток двухшинной схемы с шагом одни сутки",
        "PM-04": "объявленный набор КЗ, не произвольная сетка IEC",
        "PM-05": "карта одного луча и фазорные зоны, не терминал площадки",
        "PM-06": "классическая машина и схема Кундура, не импортированная площадка",
        "PM-07": "объявленные двигатель и привод, не заводской преобразователь",
        "PM-08": "объявленные гармоники, не сетевой расчёт площадки",
        "PM-09": "линейная оценка и локальный AC OPF",
        "PM-10": "одно включение RL и одно звено RLC, не EMT сети",
        "PM-11": "двухшинное сравнение с ANDES и колесо на этой машине",
    }
    return [
        {
            "id": claim_id,
            "state": "regression_verified",
            "network_class": "declared_example",
            "equipment": ["declared_example"],
            "scope": text,
            "field_validated": False,
        }
        for claim_id, text in scopes.items()
    ]


def qualify_profile(profile: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Сводит обязательные ворота профиля. Пропуск, устаревание и чужая схема не становятся успехом."""
    if profile not in _PROFILES:
        return {
            "profile": profile,
            "released": False,
            "verdict": "fail",
            "status": "unsupported",
            "gates": [],
            "historical": historical_claims(),
        }
    evidence = historical_claims() if records is None else records
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise ValueError("evidence must be a list of objects")
    fingerprint = package_fingerprint()
    requirements = list(_PROFILES[profile])
    if profile in {"industrial-dynamic-studies", "advanced-studies"}:
        requirements = [*_PROFILES["industrial-network-studies"], *requirements]
    gates = [_judge(requirement, evidence, fingerprint) for requirement in requirements]
    released = all(item["status"] == "pass" for item in gates)
    return {
        "profile": profile,
        "released": released,
        "verdict": "pass" if released else "fail",
        "status": "pass" if released else "fail",
        "gates": gates,
        "historical": historical_claims(),
        "small_case_promoted": False,
    }


def _judge(requirement: dict[str, Any], records: list[dict[str, Any]], fingerprint: str) -> dict[str, Any]:
    found = [item for item in records if item.get("id") == requirement["id"]]
    base = {"id": requirement["id"], "network_class": requirement["network_class"]}
    if not found:
        return {**base, "status": "missing"}
    if len(found) != 1:
        return {**base, "status": "inconclusive", "reason": "duplicate_claim"}
    record = found[0]
    if record.get("verdict", "pass") != "pass" or record.get("status", "pass") != "pass":
        return {**base, "status": "fail", "reason": "record_not_passed"}
    equipment = record.get("equipment", [])
    if not isinstance(equipment, list) or any(not isinstance(item, str) for item in equipment):
        return {**base, "status": "inconclusive", "reason": "invalid_equipment"}
    if "unsupported" in equipment:
        return {**base, "status": "unsupported"}
    if record.get("network_class") != requirement["network_class"] or record.get("state") != requirement["state"]:
        return {**base, "status": "inconclusive"}
    if record.get("code_sha256") != fingerprint:
        return {**base, "status": "stale"}
    metric = requirement["metric"]
    measured = None if not isinstance(record.get("result"), dict) else record["result"].get(metric)
    if type(measured) not in (int, float) or not math.isfinite(measured) or measured < 0:
        return {**base, "status": "inconclusive"}
    if float(measured) > float(requirement["tolerance"]):
        return {**base, "status": "fail"}
    return {**base, "status": "pass"}


def load_evidence(path: Path) -> list[dict[str, Any]]:
    """Load a local evidence bundle, checking actual artifact bytes and results.

    Integrity is not independent validation: the runner/source of the reference
    must still be qualified. Path traversal and symlinks outside the bundle are
    rejected. The artifact itself supplies measured results, not the index.
    """
    path = Path(path).resolve()
    records = json.loads(path.read_text())
    if not isinstance(records, list):
        raise ValueError("evidence index must contain a list")
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("evidence records must be objects")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("every record requires hashed artifacts")
        verified = []
        for entry in artifacts:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid artifact entry")
            candidate = (path.parent / entry["path"]).resolve()
            if not candidate.is_relative_to(path.parent) or not candidate.is_file():
                raise ValueError("artifact outside bundle or missing")
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != entry.get("sha256"):
                raise ValueError("artifact checksum mismatch")
            payload = json.loads(candidate.read_text())
            if not isinstance(payload, dict) or payload.get("verdict") != "pass":
                raise ValueError("artifact must contain a passed result")
            # Bind the measurement to the claim, scope and executing source.
            for key in ("id", "network_class", "code_sha256"):
                if payload.get(key) != record.get(key):
                    raise ValueError("artifact scope or code does not match index")
            verified.append(payload)
        measured = verified[0].get("result")
        if any(item.get("result") != measured for item in verified):
            raise ValueError("conflicting artifact measurements")
        result.append({**record, "result": measured, "verdict": "pass"})
    return result

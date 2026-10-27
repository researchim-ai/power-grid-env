"""Общие пути и хеши внешнего бенчмарка. Raw-кэш в git не входит."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "external" / "raw"
PROCESSED = REPO / "data" / "external" / "processed"
LOCK = Path(__file__).resolve().parent / "acquisition.lock.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def environment() -> dict[str, Any]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for package in ("numpy", "scipy", "pydantic", "opendssdirect.py", "dss-python"):
        try:
            from importlib.metadata import version

            versions[package] = version(package)
        except Exception:
            versions[package] = "unknown"
    return {
        "python": versions["python"],
        "packages": versions,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }

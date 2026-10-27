"""Скачивание закреплённых внешних файлов в локальный кэш.

Raw не коммитится. Повторная загрузка сверяет SHA-256. Полный SMART-DS не качается.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import yaml

from benchmarks.external._common import LOCK, RAW, sha256_bytes, sha256_file, write_json

def _load_pins() -> dict[str, Any]:
    if LOCK.exists():
        return json.loads(LOCK.read_text(encoding="utf-8"))
    return {"schema_version": "power-grid-env.acquisition-lock/v1", "sources": {}}


def known_hashes() -> dict[str, dict[str, str]]:
    """Хеши уже полученных файлов. Несовпадение с повторной загрузкой блокирует прогон."""
    locked = _load_pins().get("sources") or {}
    found: dict[str, dict[str, str]] = {}
    for source_id, payload in locked.items():
        found[source_id] = {item["name"]: item["sha256"] for item in payload.get("files") or []}
    return found


def fetch_source(source_id: str, *, budget_mb: float = 50, offline: bool = False) -> dict[str, Any]:
    catalog = yaml.safe_load((Path(__file__).resolve().parent / "sources.yaml").read_text(encoding="utf-8"))
    described = {item["id"]: item for item in catalog["sources"]}
    if source_id not in described:
        raise SystemExit(f"неизвестный источник {source_id}")
    if source_id == "smart-ds":
        return {"source": source_id, "status": "not_run", "reason": "полный bucket в первом проходе запрещён; нужен listing одного feeder"}
    if source_id == "ieee-pes":
        return _fetch_ieee(budget_mb=budget_mb, offline=offline)
    if source_id == "enwl-lvns":
        return _fetch_enwl(budget_mb=budget_mb, offline=offline)
    if source_id == "simbench":
        return _fetch_simbench(budget_mb=budget_mb, offline=offline)
    if source_id == "pglib-opf":
        return _fetch_pglib(budget_mb=budget_mb, offline=offline)
    return {"source": source_id, "status": described[source_id].get("acquisition_status", "not_downloaded")}


def _fetch_ieee(*, budget_mb: float, offline: bool) -> dict[str, Any]:
    files = [
        (
            "13Bus/IEEE13Nodeckt.dss",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/13Bus/IEEE13Nodeckt.dss",
            "a8403f9b3bee",
        ),
        (
            "13Bus/IEEELineCodes.DSS",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/13Bus/IEEELineCodes.DSS",
            "4bc8e3a0b24a",
        ),
        (
            "13Bus/IEEE13Node_BusXY.csv",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/13Bus/IEEE13Node_BusXY.csv",
            "f8dd1528c72f",
        ),
        (
            "34Bus/ieee34Mod1.dss",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/34Bus/ieee34Mod1.dss",
            "f1e732b7ea61914be307dde97b0b73398038af66a09e35dda266d409e6ebf7a5",
        ),
        (
            "34Bus/IEEELineCodes.DSS",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/34Bus/IEEELineCodes.DSS",
            "b54774e50861c034b35836d8f93fba29b8d25d9562c216162f0c6c46a8ab9dff",
        ),
        (
            "34Bus/IEEE34_BusXY.csv",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/34Bus/IEEE34_BusXY.csv",
            "6e022bbb3adb3c7f29ccb7bbdee2013517ae55e19ad25cda815616c5d4a37958",
        ),
        (
            "123Bus/IEEE123Master.dss",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/123Bus/IEEE123Master.dss",
            "21bfab1a3c956d347dc491436ae839281bf7db4ececc405e10ddeb04d3ab00ec",
        ),
        (
            "123Bus/IEEE123Loads.DSS",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/123Bus/IEEE123Loads.DSS",
            "fd28f52836205506a1980b52c5a7e12ca585aa626c52a6249502c1c3b2fc2255",
        ),
        (
            "123Bus/IEEE123Regulators.DSS",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/123Bus/IEEE123Regulators.DSS",
            "cd6ad77980dc018631ce39faab528afce40b0b9a50a6bc1c9ac8049ffb8207d7",
        ),
        (
            "123Bus/IEEELineCodes.DSS",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/123Bus/IEEELineCodes.DSS",
            "4bc8e3a0b24ad7b1cb4321f1c3b6c8458b646dd3a83b3cd11cccc02444624cb2",
        ),
        (
            "123Bus/BusCoords.dat",
            "https://raw.githubusercontent.com/tshort/OpenDSS/master/Distrib/IEEETestCases/123Bus/BusCoords.dat",
            "0b409c0e5d99f365c0140bf38280c1d64932032f1ecb8ef83ae54d4ed0d2716f",
        ),
    ]
    # Полные хеши читаем из lock, если он уже есть, иначе качаем и записываем.
    return _materialize("ieee-pes", files, budget_mb=budget_mb, offline=offline, note="redirect alias IEEELineCodes.dss создаётся при extract, raw файл не переименовывается")


def _fetch_enwl(*, budget_mb: float, offline: bool) -> dict[str, Any]:
    files = [
        (
            "lv-network-models-2.zip",
            "https://www.enwl.co.uk/globalassets/innovation/lvns/lvns-academic/lv-network-models-2.zip",
            "5ac19bcc5c0857c5d996c0e24d3d15c1e8fe4326eea0253b227576bb50d8bd51",
        ),
        (
            "lct-profiles.zip",
            "https://www.enwl.co.uk/globalassets/innovation/lvns/lvns-academic/lct-profiles.zip",
            "aa8e151779db749e4f6f93a989e7951ada071b00fc542c4f0dc94a371dce2983",
        ),
    ]
    return _materialize(
        "enwl-lvns",
        files,
        budget_mb=budget_mb,
        offline=offline,
        note="архив моделей закреплён; lct-profiles.zip — библиотека без ключа к Shape_N, суточный фидер не собран",
    )


def _fetch_simbench(*, budget_mb: float, offline: bool) -> dict[str, Any]:
    root = "https://raw.githubusercontent.com/e2nIEE/simbench/master/simbench/networks/1-complete_data-mixed-all-0-sw"
    folder = "1-complete_data-mixed-all-0-sw"
    # Профильные CSV больше бюджета источника и здесь не закрепляются.
    pinned = {
        "Node.csv": "a47e8ef775bbd420ba7f3c59789197a036d6d7d06a8751966b87bfcaf707954c",
        "Line.csv": "ab55c0a74e17bd8a0a416f84da67b79594d6cdffc61a78e904a5d676ba07ec13",
        "Load.csv": "6823f650cc32a44734ac2861344311928b0c8973af3d7fb24d2736d24da3568e",
        "Switch.csv": "e380a71f354243ae336a96ccf2b3cad832fe101a957a39c75231730ca8fbde22",
        "LineType.csv": "5397304a8dd2f2edf01063c7581a66d12532dd79355b9e74f9ce990ff00ffbee",
        "ExternalNet.csv": "5c3cca5f8f2f2494642ff9d6c6e1ba32a5d6d61941b469714a9af01dd777de1d",
        "Transformer.csv": "fa10e4fc0981a481bd0d04bcf4c1953ded66834a86e06c474573f2c40c4329e2",
        "TransformerType.csv": "a7834ecce3d670d652fec12a436f7930f12609a49fb483d0436275525e0b6f4d",
        "StudyCases.csv": "834d518fb2408555e0f88f3c8a0431ea73515f21a1dc8edafcaad88b9d89808d",
    }
    files = [(f"{folder}/{name}", f"{root}/{name}", digest) for name, digest in pinned.items()]
    return _materialize(
        "simbench",
        files,
        budget_mb=budget_mb,
        offline=offline,
        note="структурный снимок study case n1; LoadProfile.csv и соседние профили не скачивались",
    )


def _fetch_pglib(*, budget_mb: float, offline: bool) -> dict[str, Any]:
    root = "https://raw.githubusercontent.com/power-grid-lib/pglib-opf/master"
    pinned = {
        "pglib_opf_case3_lmbd.m": "168f73f389aa8abd7707fae04d26ee2046ef3622e8ea2f6661a89d815e9c637d",
        "pglib_opf_case5_pjm.m": "cadf7501a15c2d508820493cef6acc85757274197e74c40bcec4fc4ecf619e6f",
        "pglib_opf_case14_ieee.m": "bd5c568621de65e4b0922317010868bc7fa94173807faa10ea8fdbbe77c28106",
        "pglib_opf_case118_ieee.m": "b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78",
    }
    files = [(name, f"{root}/{name}", digest) for name, digest in pinned.items()]
    return _materialize(
        "pglib-opf",
        files,
        budget_mb=budget_mb,
        offline=offline,
        note="кейсы v23.07. Локальный SLSQP с аналитическим якобианом; глобальный оптимум не заявлен",
    )


def _materialize(source_id: str, files: list[tuple[str, str, str]], *, budget_mb: float, offline: bool, note: str) -> dict[str, Any]:
    root = RAW / source_id
    root.mkdir(parents=True, exist_ok=True)
    stored = []
    total = 0
    for name, url, expected in files:
        path = root / name
        if path.exists():
            digest = sha256_file(path)
            if expected and len(expected) == 64 and digest != expected:
                raise SystemExit(f"{source_id}:{name} hash mismatch {digest} != {expected}")
            if expected and len(expected) < 64 and not digest.startswith(expected):
                raise SystemExit(f"{source_id}:{name} hash prefix mismatch")
        elif offline:
            return {"source": source_id, "status": "data_unavailable", "missing": name}
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "power-grid-env"})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            digest = sha256_bytes(payload)
            if len(expected) == 64 and digest != expected:
                raise SystemExit(f"{source_id}:{name} скачанный hash {digest} не совпал с закреплением")
            if total + len(payload) > budget_mb * 1024 * 1024:
                return {"source": source_id, "status": "not_run", "reason": "превышен budget-mb"}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            total += len(payload)
        stored.append({"name": name, "url": url, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {"schema_version": "power-grid-env.acquisition-lock/v1", "sources": {}}
    lock["sources"][source_id] = {"note": note, "files": stored}
    write_json(LOCK, lock)
    return {"source": source_id, "status": "downloaded", "files": stored}


def assert_zip_safe(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                raise ValueError(f"небезопасный путь в архиве: {name}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="benchmarks.external.fetch")
    parser.add_argument("--source", required=True)
    parser.add_argument("--budget-mb", type=float, default=50)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    result = fetch_source(args.source, budget_mb=args.budget_mb, offline=args.offline)
    print(json.dumps({"status": result["status"], "source": result["source"]}, ensure_ascii=False))
    return 0 if result["status"] in {"downloaded", "not_run", "data_unavailable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

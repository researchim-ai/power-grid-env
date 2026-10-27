"""Инвентарь профилей ENWL. Библиотека LCT не склеивается с фидером.

LoadShapes моделей ссылаются на отсутствующие CSV с абсолютными путями Windows.
lct-profiles.zip — шесть книг без заголовков и без ключа Shape_N. Колонка книги
не назначается нагрузке фидера.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from benchmarks.external._common import RAW, sha256_file, write_json

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MODELS = RAW / "enwl-lvns" / "lv-network-models-2.zip"
PROFILES = RAW / "enwl-lvns" / "lct-profiles.zip"
SAMPLE_SHAPES = "LV network models/network_23/Feeder_3/LoadShapes.txt"


def inventory() -> dict:
    if not PROFILES.exists() or not MODELS.exists():
        return {
            "status": "data_unavailable",
            "reason": "нет локального архива моделей или lct-profiles.zip",
            "feeder_day": "data_unavailable",
        }
    books = []
    with zipfile.ZipFile(PROFILES) as archive:
        for name in archive.namelist():
            if not name.endswith(".xlsx"):
                continue
            books.append(_book(name, archive.read(name)))
    sample = _sample_shape_paths()
    joinable = False
    return {
        "status": "data_unavailable",
        "feeder_day": "data_unavailable",
        "models_sha256": sha256_file(MODELS),
        "profiles_sha256": sha256_file(PROFILES),
        "profiles_bytes": PROFILES.stat().st_size,
        "shape_files_referenced": sample,
        "join_key_found": joinable,
        "books": books,
        "evidence": "real_network_model",
        "profile_evidence": "crest_lct_library_not_field_measurements",
        "note": (
            "Суточный QSTS фидера не собран: в книгах нет имени фидера, номера Shape_N "
            "и пути LoadN_5min.csv. Плоский множитель вместо отсутствующего профиля не подставлялся."
        ),
    }


def _book(name: str, payload: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(payload)) as book:
        shared = "xl/sharedStrings.xml" in book.namelist()
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    dimension = sheet.find(f"{_NS}dimension")
    ref = None if dimension is None else dimension.attrib.get("ref")
    return {
        "name": name,
        "dimension": ref,
        "shared_strings": shared,
        "header": False,
        "rows": 288,
        "columns": 100,
        "step_min": 5,
        "customer_or_feeder_key": None,
    }


def _sample_shape_paths() -> list[str]:
    with zipfile.ZipFile(MODELS) as archive:
        text = archive.read(SAMPLE_SHAPES).decode("utf-8", errors="replace")
    found = []
    for line in text.splitlines():
        if "file=" not in line:
            continue
        found.append(line.split("file=", 1)[1].split(")", 1)[0])
        if len(found) == 2:
            break
    return found


def write_report(out: Path) -> dict:
    payload = inventory()
    write_json(out / "profiles.json", payload)
    return payload


if __name__ == "__main__":
    print(write_report(Path("reports/external/enwl"))["feeder_day"])

"""Прямой расчёт исходного DSS в OpenDSS, без импортёра и без нашего рендера.

Это conversion_consistency на DSS-Extensions, не независимый решатель.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from benchmarks.external._common import PROCESSED, sha256_file, write_json


def extract_master(master: Path, out: Path, *, evidence: str) -> dict[str, Any]:
    master = master.resolve()
    from opendssdirect.OpenDSSDirect import OpenDSSDirect

    work = _prepare_workdir(master)
    session = OpenDSSDirect().NewContext()
    session.Text.Command("clear")
    target = work / master.name
    previous = Path.cwd()
    derived_note = None
    try:
        session.Text.Command(f'compile "{target.resolve()}"')
        _solve_tight(session)
    except Exception as exc:
        derived = _derived_script(target)
        if derived is None:
            payload = _failed(master, evidence, str(exc))
            write_json(out, payload)
            return payload
        derived_path = work / "derived_master.dss"
        derived_path.write_text(derived, encoding="utf-8")
        _drop_missing_daily_shapes(work)
        try:
            session.Text.Command("clear")
            session.Text.Command(f'compile "{derived_path.resolve()}"')
            _solve_tight(session)
            derived_note = (
                "исходный master не собрался. Повтор: добавлен New Circuit, "
                "Redirect LoadShapes снят, потому что CSV профилей в архиве нет. "
                "Это снимок паспортных kW, не сутки профиля."
            )
        except Exception as second:
            payload = _failed(master, evidence, f"{exc} | derived: {second}")
            write_json(out, payload)
            return payload
    finally:
        os.chdir(previous)
    names = list(session.Circuit.AllBusNames())
    if names:
        session.Circuit.SetActiveBus(names[0])
        sample = list(session.Bus.puVmagAngle())
        if sample and sample[0] > 3.0:
            session.Text.Command("Calcvoltagebases")
            _solve_tight(session)
    buses = []
    for name in session.Circuit.AllBusNames():
        session.Circuit.SetActiveBus(name)
        nodes = list(session.Bus.Nodes())
        pu = list(session.Bus.puVmagAngle())
        phases = []
        for index, node in enumerate(nodes):
            if index * 2 + 1 >= len(pu):
                break
            phases.append({"node": int(node), "pu": float(pu[index * 2]), "angle_deg": float(pu[index * 2 + 1])})
        buses.append({"name": name, "phases": phases})
    losses = session.Circuit.Losses()
    payload = {
        "evidence": evidence,
        "master": master.name,
        "master_sha256": sha256_file(master),
        "converged": bool(session.Solution.Converged()),
        "iterations": int(session.Solution.Iterations()),
        "losses_w": float(losses[0]) if losses else None,
        "losses_var": float(losses[1]) if losses and len(losses) > 1 else None,
        "buses": buses,
        "engine_note": "DSS-Extensions compile исходного файла. Тот же C-engine, что у адаптера; независимость частичная.",
        "derived_note": derived_note,
        "filesystem_note": "для IEEE13 создан alias IEEELineCodes.dss -> IEEELineCodes.DSS, содержимое не менялось",
    }
    write_json(out, payload)
    return payload


def _solve_tight(session) -> None:
    """Тот же порядок допуска, что у адаптера. Штатный 1e-4 OpenDSS шире порога 1e-5."""
    session.Text.Command("Set tolerance=1e-10")
    session.Text.Command("Set maxiterations=200")
    session.Solution.Solve()


def _failed(master: Path, evidence: str, error: str) -> dict[str, Any]:
    return {
        "evidence": evidence,
        "master": master.name,
        "master_sha256": sha256_file(master),
        "converged": False,
        "error": error,
        "engine_note": "native compile/solve, renderer платформы не вызывался",
    }


def _derived_script(master: Path) -> str | None:
    text = master.read_text(encoding="utf-8", errors="replace")
    if "new circuit" in text.lower() and "loadshapes" not in text.lower():
        return None
    lines = []
    if "new circuit" not in text.lower():
        lines.append("New Circuit.derived basekv=11 pu=1 phases=3 bus1=SourceBus")
    for line in text.splitlines():
        if line.strip().lower().startswith("redirect") and "loadshape" in line.lower():
            lines.append("! derived: Redirect LoadShapes снят, CSV профилей нет в архиве")
            continue
        lines.append(line)
    bases = _voltage_bases(master.parent)
    if bases:
        lines.append("Set voltagebases=[" + " ".join(bases) + "]")
        lines.append("Calcvoltagebases")
    return "\n".join(lines) + "\n"


def _voltage_bases(directory: Path) -> list[str]:
    import re

    found: list[str] = []
    for path in directory.glob("*.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"(?i)\bkvs?\s*=\s*[\[(]([^\]\)]+)", text):
            for token in match.group(1).replace(",", " ").split():
                if token not in found:
                    found.append(token)
    return found


def _drop_missing_daily_shapes(work: Path) -> None:
    import re

    for path in work.glob("*.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "daily=" not in text.lower():
            continue
        if path.is_symlink():
            path.unlink()
        path.write_text(re.sub(r"(?i)\s+daily=\S+", "", text), encoding="utf-8")


def _prepare_workdir(master: Path) -> Path:
    work = PROCESSED / "native" / master.parent.name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    # Копия, не ссылка: OpenDSS ищет Redirect рядом с целью symlink, то есть в raw.
    for item in master.parent.iterdir():
        if item.is_file() and not item.is_symlink():
            shutil.copy2(item, work / item.name)
    _alias_redirects(work, work / master.name)
    return work


def _alias_redirects(work: Path, master: Path) -> None:
    import re

    if not master.exists():
        return
    text = master.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"(?i)\bredirect\s+(\S+)", text):
        target = match.group(1).strip().strip('"')
        wanted = work / target
        if wanted.exists():
            continue
        matches = [item for item in work.iterdir() if item.is_file() and item.name.lower() == Path(target).name.lower()]
        if len(matches) == 1 and not wanted.exists():
            wanted.symlink_to(matches[0].name)

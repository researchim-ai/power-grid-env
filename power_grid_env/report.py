"""JSON, HTML и комплект доказательств. HTML не обращается к сети."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_html_report(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(_report_html(summary), encoding="utf-8")


def write_manifest(directory: Path) -> dict[str, Any]:
    files = []
    for file in sorted(directory.rglob("*")):
        if not file.is_file() or file.name == "manifest.json":
            continue
        files.append({"path": str(file.relative_to(directory)), "sha256": sha256_file(file)})
    manifest = {
        "schema_version": "power-grid-env.evidence/v1",
        "files": files,
        "note": "Платформа не выдаёт отраслевой сертификат и не гарантирует управление реальной сетью.",
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def _report_html(summary: dict[str, Any]) -> str:
    rows = []
    for item in summary.get("comparisons", []):
        worse = ", ".join(item.get("candidate_worse_on") or []) or "—"
        rows.append(
            "<tr><td>{scenario}</td><td>{base}</td><td>{cand}</td><td>{worse}</td><td>{status}</td></tr>".format(
                scenario=item.get("scenario_id"),
                base=_fmt_ens(item.get("baseline")),
                cand=_fmt_ens(item.get("candidate")),
                worse=worse,
                status=item.get("pair_status"),
            )
        )
    failures = "".join(f"<li>{item}</li>" for item in summary.get("failures", [])) or "<li>нет</li>"
    unassessed = "".join(f"<li>{item}</li>" for item in summary.get("unassessed", [])) or "<li>нет</li>"
    assumptions = "".join(f"<li>{item}</li>" for item in summary.get("assumptions", [])) or "<li>нет</li>"
    scope = summary.get("applicability", "")
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Отчёт испытания</title>
<style>
body {{ font-family: sans-serif; margin: 24px; color: #1b1b1b; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
.banner {{ background: #13322b; color: white; padding: 12px 16px; }}
.note {{ background: #f4f1ea; padding: 12px; }}
</style></head><body>
<div class="banner">Отчёт сравнения с baseline. Это не сертификат и не допуск к управлению сетью.</div>
<p>{summary.get("title", "Испытание восстановления")}</p>
<div class="note"><b>Область применимости.</b> {scope}</div>
<h2>Парные результаты</h2>
<table><tr><th>Сценарий</th><th>Baseline, недоотпуск кВт·ч</th><th>Кандидат</th><th>Где кандидат хуже</th><th>Статус</th></tr>
{''.join(rows)}
</table>
<h2>Сбои</h2><ul>{failures}</ul>
<h2>Неоценённые случаи</h2><ul>{unassessed}</ul>
<h2>Допущения</h2><ul>{assumptions}</ul>
<p>Вердикт набора: <b>{summary.get("verdict", "inconclusive")}</b>. {summary.get("verdict_reason", "")}</p>
</body></html>
"""


def _fmt_ens(metrics: dict[str, Any] | None) -> str:
    if not metrics or metrics.get("energy_not_served_wh") is None:
        return "не оценено"
    value = float(metrics["energy_not_served_wh"]) / 1000.0
    mark = " (нижняя граница)" if metrics.get("incomplete") else ""
    return f"{value:.4f}{mark}"

"""Команды import, preflight, suite, validate, accept, view."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from power_grid_env import __version__
from power_grid_env.importer import import_network
from power_grid_env.preflight import preflight
from power_grid_env.report import write_json
from power_grid_env.runner import run_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="power-grid", description="Инженерная проверка алгоритмов распределительных сетей")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="импорт DSS+sidecar или JSON")
    imp.add_argument("--source", required=True)
    imp.add_argument("--sidecar")
    imp.add_argument("--out", required=True)
    imp.add_argument("--report", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("model")
    pre.add_argument("--report", required=True)

    suite = sub.add_parser("suite")
    suite.add_argument("path")
    suite.add_argument("--out", required=True)
    suite.add_argument("--in-process", action="store_true")
    suite.add_argument("--resume", action="store_true")

    sub.add_parser("validate")
    sub.add_parser("accept")

    view = sub.add_parser("view")
    view.add_argument("trace")
    view.add_argument("--network", required=True)
    view.add_argument("--out", required=True)

    schemas = sub.add_parser("schemas", help="выгрузить JSON Schema сети и сценария")
    schemas.add_argument("--out", required=True)

    pilot = sub.add_parser("pilot", help="один шаг пилота по каталогу модели")
    pilot.add_argument("model")
    pilot.add_argument("--out", required=True)

    study = sub.add_parser("study", help="импорт, прогон и публичный вид одного исследования")
    study_commands = study.add_subparsers(dest="study_command", required=True)
    study_import = study_commands.add_parser("import")
    study_import.add_argument("--source", required=True)
    study_import.add_argument("--sidecar")
    study_import.add_argument("--dir", required=True)
    study_run = study_commands.add_parser("run")
    study_run.add_argument("--dir", required=True)
    study_run.add_argument("--steps", type=int, default=1)
    study_public = study_commands.add_parser("public")
    study_public.add_argument("--dir", required=True)

    qualify = sub.add_parser("qualify", help="снимок готовности без заявления о выпуске")
    qualify.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "import":
        network, report = import_network(Path(args.source), Path(args.sidecar) if args.sidecar else None)
        write_json(Path(args.report), report)
        if network is None:
            print("импорт отклонён", file=sys.stderr)
            return 2
        Path(args.out).write_text(network.model_dump_json(indent=2), encoding="utf-8")
        print(args.out)
        return 0
    if args.command == "preflight":
        from power_grid_env.model import Network

        network = Network.model_validate_json(Path(args.model).read_text(encoding="utf-8"))
        report = preflight(network)
        write_json(Path(args.report), report)
        print(json.dumps({"ok": report["ok"], "errors": report["errors"]}, ensure_ascii=False))
        return 0 if report["ok"] else 2
    if args.command == "suite":
        summary = run_suite(Path(args.path), Path(args.out), use_process_backend=not args.in_process, resume=args.resume)
        print(summary["verdict"])
        return 0 if summary["verdict"] in {"pass", "completed_with_recorded_regressions"} else 1
    if args.command == "validate":
        return _validate()
    if args.command == "accept":
        from power_grid_env.acceptance import run_acceptance

        return run_acceptance()
    if args.command == "view":
        from power_grid_env.model import Network
        from power_grid_env.viewer import viewer_bundle, write_viewer

        network = Network.model_validate_json(Path(args.network).read_text(encoding="utf-8"))
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        write_viewer(Path(args.out), viewer_bundle(network.model_dump(mode="json"), trace, 0.95, 1.05))
        print(args.out)
        return 0
    if args.command == "schemas":
        from power_grid_env.schema_export import export_schemas

        export_schemas(Path(args.out))
        print(args.out)
        return 0
    if args.command == "pilot":
        from power_grid_env.runner import _load_model
        from power_grid_env.study import run_pilot

        network, report = _load_model(Path(args.model))
        if network is None:
            write_json(Path(args.out), {"ok": False, "import": report})
            return 2
        payload = run_pilot(network)
        payload.pop("trace", None)
        payload["ok"] = payload["solve_status"] in {"converged", "deenergized_no_source"} and payload["time_s"] == payload["resumed_time_s"]
        write_json(Path(args.out), payload)
        print(json.dumps({"ok": payload["ok"], "status": payload["solve_status"]}, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    if args.command == "study":
        from power_grid_env.workspace import StudyWorkspace

        workspace = StudyWorkspace(Path(args.dir))
        if args.study_command == "import":
            payload = workspace.import_source(Path(args.source), Path(args.sidecar) if args.sidecar else None)
            print(json.dumps({"ok": payload["ok"]}, ensure_ascii=False))
            return 0 if payload["ok"] else 2
        if args.study_command == "run":
            payload = workspace.run(steps=args.steps)
            print(json.dumps({"ok": payload["ok"], "status": payload["status"], "time_s": payload["time_s"]}, ensure_ascii=False))
            return 0 if payload["ok"] else 1
        payload = workspace.public_view()
        print(json.dumps({"time_s": payload["time_s"], "steps": len(payload["steps"])}, ensure_ascii=False))
        return 0
    if args.command == "qualify":
        from power_grid_env.qualify import write_qualification

        payload = write_qualification(Path(args.out))
        print(json.dumps({"released": payload["released"]}, ensure_ascii=False))
        return 0
    return 2


def _validate() -> int:
    from power_grid_env.numerical_suite import run_numerical_suite

    results = run_numerical_suite()
    out = Path("reports/validation")
    out.mkdir(parents=True, exist_ok=True)
    payload = {"version": __version__, "checks": results, "passed": all(item["passed"] for item in results)}
    write_json(out / "numerical.json", payload)
    lines = ["# Численный отчёт", "", f"Версия {__version__}.", ""]
    for item in results:
        mark = "пройдено" if item["passed"] else "не пройдено"
        lines.append(f"- {item['id']}: {mark}. {item['detail']}")
    lines.append("")
    lines.append("Совпадение с positive-sequence не использовалось как доказательство несимметрии.")
    lines.append("Несходимость PQ не названа коллапсом.")
    (out / "numerical.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": payload["passed"], "n": len(results)}, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

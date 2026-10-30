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
    qualify.add_argument("--profile", default="")
    qualify.add_argument("--evidence", help="JSON index of hashed evidence artifacts")

    plant = sub.add_parser("plant-study", help="замороженный набор режимов площадки")
    plant.add_argument("--out", required=True)

    run_spec = sub.add_parser("run-study", help="запуск сохранённого исследования")
    run_spec.add_argument("spec")
    run_spec.add_argument("--out", required=True)
    run_spec.add_argument("--resume", action="store_true")
    run_spec.add_argument("--max-steps", type=int)
    run_spec.add_argument("--checkpoint-every", type=int, default=96)
    run_spec.add_argument("--cancel-file")
    run_spec.add_argument("--isolate", action="store_true", help="RMS worker with wall deadline")
    run_spec.add_argument("--wall-timeout", type=float, default=None)

    compare_motor = sub.add_parser("compare-motor-trace", help="сверка motor study с полной SI CSV-трассой")
    for argument in ("study", "result", "trace", "protocol", "out"):
        compare_motor.add_argument("--"+argument, required=True)

    sensitivity = sub.add_parser("motor-sensitivity", help="ограниченный параметрический эксперимент motor study")
    for argument in ("study", "protocol", "out"):
        sensitivity.add_argument("--"+argument, required=True)
    sensitivity.add_argument("--cancel-file")

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
        if args.profile:
            from power_grid_env.evidence import qualify_profile

            from power_grid_env.evidence import load_evidence

            try:
                records = load_evidence(Path(args.evidence)) if args.evidence else None
                payload = qualify_profile(args.profile, records)
            except (ValueError, OSError) as exc:
                payload = {"profile": args.profile, "released": False, "verdict": "fail",
                           "status": "invalid_evidence", "error": str(exc)}
            write_json(Path(args.out) / "profile.json", payload)
            print(json.dumps({"profile": payload["profile"], "released": payload["released"], "verdict": payload["verdict"]}, ensure_ascii=False))
            return 0 if payload["verdict"] == "pass" else 1
        if args.evidence:
            parser.error("--evidence requires --profile")
        from power_grid_env.qualify import write_qualification

        payload = write_qualification(Path(args.out))
        print(json.dumps({"released": payload["released"]}, ensure_ascii=False))
        return 0
    if args.command == "plant-study":
        from power_grid_env.plant_study import run_plant_study

        payload = run_plant_study()
        write_json(Path(args.out) / "plant-study.json", payload)
        print(json.dumps({"verdict": payload["verdict"], "scenarios": payload["scenario_count"], "profile_released": payload["profile_released"]}, ensure_ascii=False))
        return 0 if payload["verdict"] == "pass" else 1
    if args.command == "motor-sensitivity":
        from power_grid_env.motor_sensitivity import MotorSensitivityProtocol, run_motor_sensitivity
        from power_grid_env.study_spec import StudySpec
        try:
            spec = StudySpec.model_validate_json(Path(args.study).read_text())
            protocol = MotorSensitivityProtocol.model_validate_json(Path(args.protocol).read_text())
            result = run_motor_sensitivity(spec, protocol, Path(args.out),
                cancelled=(lambda: Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (OSError, ValueError, TypeError) as exc:
            result = {"completed": False, "all_cases_within_limits": False, "status": "rejected", "error": str(exc)}
            print(str(exc)[:4000], file=sys.stderr)
        print(json.dumps({k:result[k] for k in ("completed", "all_cases_within_limits", "status")}, ensure_ascii=False))
        if result['status'] in {'cancelled', 'total_timeout'}:
            return 3
        return 0 if result['completed'] and result['all_cases_within_limits'] else 2

    if args.command == "compare-motor-trace":
        from power_grid_env.motor_trace import MotorTraceProtocol, compare_motor_trace
        from power_grid_env.study_spec import StudySpec
        try:
            spec = StudySpec.model_validate_json(Path(args.study).read_text())
            result = json.loads(Path(args.result).read_text())
            protocol = MotorTraceProtocol.model_validate_json(Path(args.protocol).read_text())
            comparison = compare_motor_trace(spec, result, Path(args.trace), protocol)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            comparison = {"completed": False, "passed": False, "status": "rejected", "error": str(exc)}
        write_json(Path(args.out), comparison)
        print(json.dumps({"passed": comparison["passed"], "status": comparison["status"]}, ensure_ascii=False))
        return 0 if comparison["passed"] else 2

    if args.command == "run-study":
        from power_grid_env.study_spec import run_study_document

        qsts = False
        try:
            payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("study document must be an object")
            qsts = payload.get("study", payload.get("kind")) == "qsts"
            report = run_study_document(payload, out=Path(args.out) if qsts else None,
                                        resume=args.resume, max_steps=args.max_steps,
                                        checkpoint_every=args.checkpoint_every, isolate=args.isolate, wall_timeout_s=args.wall_timeout,
                                        cancelled=(lambda: Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (OSError, ValueError, RuntimeError) as exc:
            report = {"solved": False, "completed": False, "status": "failed", "error": str(exc)}
        # Never replace an existing successful result on a failed resume/open.
        if not qsts:
            write_json(Path(args.out), report)
        elif report.get("status") in {"failed", "rejected"}:
            write_json(Path(args.out) / "error.json", report)
        print(json.dumps({"solved": report["solved"], "status": report["status"]}, ensure_ascii=False))
        if report.get("status") in {"paused", "cancelled"}:
            return 3
        return 0 if report.get("completed", report["solved"]) else 2
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

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

    modes=sub.add_parser('critical-modes',help='bounded analytic Schur critical eigenmodes')
    modes.add_argument('study');modes.add_argument('--out',required=True);modes.add_argument('--count',type=int,default=6);modes.add_argument('--indices')

    dol=sub.add_parser('industrial-dol',help='bounded industrial DOL/restart study pack')
    dol.add_argument('pack');dol.add_argument('--out',required=True);dol.add_argument('--cancel-file')
    fit=sub.add_parser('identify-motor',help='bounded steady Gamma fit with separate held-out observations')
    fit.add_argument('observations');fit.add_argument('--out',required=True)

    run_spec = sub.add_parser("run-study", help="запуск сохранённого исследования")
    run_spec.add_argument("spec")
    run_spec.add_argument("--out", required=True)
    run_spec.add_argument("--resume", action="store_true")
    run_spec.add_argument("--max-steps", type=int)
    run_spec.add_argument("--checkpoint-every", type=int, default=96)
    run_spec.add_argument("--cancel-file")
    run_spec.add_argument("--isolate", action="store_true", help="RMS worker with wall deadline")
    run_spec.add_argument("--wall-timeout", type=float, default=None)

    clearing = sub.add_parser("fault-clearing", help="конечный эксперимент времён отключения coupled КЗ")
    for argument in ("study", "protocol", "out"):
        clearing.add_argument("--"+argument, required=True)
    clearing.add_argument("--cancel-file")

    bundle=sub.add_parser("motor-bundle",help="переносимый пакет motor study с проверкой и replay")
    bundle_commands=bundle.add_subparsers(dest="bundle_command",required=True)
    create=bundle_commands.add_parser("create")
    for argument in ("study","criteria","out"):create.add_argument("--"+argument,required=True)
    create.add_argument("--trace");create.add_argument("--trace-protocol");create.add_argument("--cancel-file")
    for command in ("verify","replay"):
        check=bundle_commands.add_parser(command);check.add_argument("bundle");check.add_argument("--out",required=True)
        if command=="replay":check.add_argument("--cancel-file")

    compare_motor = sub.add_parser("compare-motor-trace", help="сверка motor study с полной SI CSV-трассой")
    for argument in ("study", "result", "trace", "protocol", "out"):
        compare_motor.add_argument("--"+argument, required=True)

    sensitivity = sub.add_parser("motor-sensitivity", help="ограниченный параметрический эксперимент motor study")
    for argument in ("study", "protocol", "out"):
        sensitivity.add_argument("--"+argument, required=True)
    sensitivity.add_argument("--cancel-file")

    restart = sub.add_parser("motor-restart",help="конечное сравнение выдержек самозапуска")
    for argument in ("study","protocol","out"):restart.add_argument("--"+argument,required=True)
    restart.add_argument("--cancel-file")

    for name in ('motor-restart-check','motor-restart-replay'):
        command=sub.add_parser(name,help='проверка сохранённой серии самозапуска' if name.endswith('check') else 'полный replay серии самозапуска')
        command.add_argument('directory');command.add_argument('--out',required=True)
        if name.endswith('replay'):command.add_argument('--cancel-file')

    workbench=sub.add_parser('workbench',help='local versioned engineering projects and offline reports')
    wb=workbench.add_subparsers(dest='workbench_command',required=True)
    for name in ['create','revise']:
        cmd=wb.add_parser(name);cmd.add_argument('study');cmd.add_argument('--dir',required=True)
    cmd=wb.add_parser('import');cmd.add_argument('--source',required=True);cmd.add_argument('--sidecar');cmd.add_argument('--dir',required=True)
    for name in ['check','status','cancel','clear-cancel']:
        cmd=wb.add_parser(name);cmd.add_argument('--dir',required=True)
        if name=='check':cmd.add_argument('--revision')
    cmd=wb.add_parser('run');cmd.add_argument('run_id');cmd.add_argument('--dir',required=True);cmd.add_argument('--revision');cmd.add_argument('--resume-from');cmd.add_argument('--max-steps',type=int);cmd.add_argument('--timeout',type=float,default=120.);cmd.add_argument('--checkpoint-every',type=int,default=96)
    for name in ['verify','export']:
        cmd=wb.add_parser(name);cmd.add_argument('run_id');cmd.add_argument('--dir',required=True)
        if name=='export':cmd.add_argument('--out',required=True)
    cmd=wb.add_parser('restore');cmd.add_argument('archive');cmd.add_argument('--dir',required=True)
    cmd=wb.add_parser('compare');cmd.add_argument('baseline');cmd.add_argument('candidate');cmd.add_argument('--dir',required=True);cmd.add_argument('--out',required=True);cmd.add_argument('--absolute',type=float,default=1e-8);cmd.add_argument('--relative',type=float,default=1e-6);cmd.add_argument('--time-tolerance',type=float,default=1e-9)

    steady = sub.add_parser('steady-study', help='shared balanced AC PF/OPF/estimation/N-1 studies')
    commands = steady.add_subparsers(dest='steady_command', required=True)
    cmd = commands.add_parser('import-matpower'); cmd.add_argument('--source', required=True); cmd.add_argument('--out', required=True)
    cmd = commands.add_parser('derive'); cmd.add_argument('directory'); cmd.add_argument('--task', choices=['power_flow', 'n1'], required=True); cmd.add_argument('--out', required=True)
    cmd = commands.add_parser('run'); cmd.add_argument('--study', required=True); cmd.add_argument('--out', required=True)
    cmd.add_argument('--resume-from'); cmd.add_argument('--max-cases', type=int); cmd.add_argument('--cancel-file'); cmd.add_argument('--time-budget', type=float)
    for name in ['verify', 'export']:
        cmd = commands.add_parser(name); cmd.add_argument('directory')
        if name == 'export': cmd.add_argument('--out', required=True)

    phase = sub.add_parser('phase-study', help='SI phase-domain networks, physical search and guarded surrogates')
    phase_commands = phase.add_subparsers(dest='phase_command', required=True)
    cmd = phase_commands.add_parser('initialize'); cmd.add_argument('--study', required=True); cmd.add_argument('--out', required=True); cmd.add_argument('--duration', type=float, required=True); cmd.add_argument('--sample', type=float, required=True); cmd.add_argument('--max-step', type=float, required=True)
    cmd = phase_commands.add_parser('run'); cmd.add_argument('--study', required=True); cmd.add_argument('--out', required=True)
    cmd = phase_commands.add_parser('search'); cmd.add_argument('--protocol', required=True); cmd.add_argument('--out', required=True)
    cmd.add_argument('--max-points', type=int); cmd.add_argument('--resume-from'); cmd.add_argument('--cancel-file')
    cmd = phase_commands.add_parser('train'); cmd.add_argument('directory'); cmd.add_argument('--out', required=True)
    cmd.add_argument('--max-error', type=float, required=True); cmd.add_argument('--degree', type=int, choices=[1,2], default=2); cmd.add_argument('--seed', type=int, default=41)
    cmd = phase_commands.add_parser('predict'); cmd.add_argument('directory'); cmd.add_argument('--point', required=True); cmd.add_argument('--out', required=True); cmd.add_argument('--screen-only', action='store_true')
    for name in ['verify', 'export']:
        cmd = phase_commands.add_parser(name); cmd.add_argument('directory')
        if name == 'export': cmd.add_argument('--out', required=True)

    for name in ['protected-motor','sensitivity-study']:
        cmd=sub.add_parser(name,help='full-winding protected DOL workflow' if name=='protected-motor' else 'audited full-study hybrid parameter derivatives')
        cmd.add_argument('--out',required=True)
        if name=='sensitivity-study':
            cmd.add_argument('--study',required=True);cmd.add_argument('--protocol',required=True)
        else:
            from power_grid_env.motor_catalog import CATALOG_IDS
            choice=cmd.add_mutually_exclusive_group(required=True);choice.add_argument('--study');choice.add_argument('--catalog',choices=CATALOG_IDS)
            cmd.add_argument('--mode',choices=['restart','locked','temperature','cool_reclose'],default='restart');cmd.add_argument('--require-acceptable',action='store_true')

    campaign=sub.add_parser('campaign',help='bounded reproducible engineering campaigns')
    actions=campaign.add_subparsers(dest='campaign_action',required=True)
    cmd=actions.add_parser('run');cmd.add_argument('--plan',required=True);cmd.add_argument('--out',required=True);cmd.add_argument('--resume-from');cmd.add_argument('--recover-from');cmd.add_argument('--cancel-file')
    cmd=actions.add_parser('grid');cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    for name in ['verify','export','import']:
        cmd=actions.add_parser(name);cmd.add_argument('source')
        if name!='verify':cmd.add_argument('--out',required=True)

    cmd=sub.add_parser('identify-dynamic-motor',help='dynamic Gamma identification with free held-out replay')
    inputs=cmd.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input');inputs.add_argument('--recordings',help='sealed CSV import directory, semantically verified before solving')
    cmd.add_argument('--out',required=True)

    cmd=sub.add_parser('motor-recordings',help='audited synchronous CSV preparation for dynamic motor identification')
    actions=cmd.add_subparsers(dest='recording_action',required=True)
    cmd=actions.add_parser('import');cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=actions.add_parser('verify');cmd.add_argument('directory')

    cmd=sub.add_parser('audit-dynamic-motor',help='raw-observation residual audit with explicit training-speed processing')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)

    cmd=sub.add_parser('calibrated-motor',help='known-calibration CSV import and raw-observation motor audit')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)

    cmd=sub.add_parser('anchor-dynamic-motor',help='independent bounded DC stator resistance and three conditional raw audits')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)

    cmd=sub.add_parser('llm-data',help='physical LLM corpus, SFT exports and GRPO plan rewards')
    actions=cmd.add_subparsers(dest='llm_data_action',required=True)
    cmd=actions.add_parser('generate');cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=actions.add_parser('verify');cmd.add_argument('directory')
    cmd=actions.add_parser('export');cmd.add_argument('directory');cmd.add_argument('--out',required=True);cmd.add_argument('--split',choices=['train','dev','test'],default='train');cmd.add_argument('--kind',choices=['prompts','plans','steps','tools'],default='plans')
    cmd=actions.add_parser('score-group');cmd.add_argument('directory');cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True);cmd.add_argument('--split',choices=['train','dev','test'],default='train')
    cmd=actions.add_parser('evaluate');cmd.add_argument('directory');cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True);cmd.add_argument('--split',choices=['dev','test'],default='test')

    cmd=sub.add_parser('motor-engineering',help='three-grid DOL/restart shaft-energy and I2t study')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-motor-engineering',help='verify sealed three-grid motor engineering artifacts')
    cmd.add_argument('directory')

    cmd=sub.add_parser('verify-comparison',help='recompute cross-simulator coverage/errors from sealed normalized traces')
    cmd.add_argument('directory')

    cmd=sub.add_parser('motor-robustness',help='finite parameter design with three-grid engineering audit per point')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-motor-robustness',help='recompute finite-design engineering evidence')
    cmd.add_argument('directory')

    cmd=sub.add_parser('motor-transfer',help='explicit RMS/Gamma single-cage passport referral and omission report')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-motor-transfer',help='recompute sealed passport referral')
    cmd.add_argument('directory')

    cmd=sub.add_parser('warm-flux-study',help='periodic full-winding network initialization and study')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-warm-flux-study',help='verify warm-state equilibrium and trajectory integrity')
    cmd.add_argument('directory')

    cmd=sub.add_parser('ac-continuation',help='bounded adaptive load interpolation with Newton rollback')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-ac-continuation',help='recompute continuation step balance and rollback checks')
    cmd.add_argument('directory')

    cmd=sub.add_parser('ac-pseudo-arc',help='sparse tangent/hyperplane AC branch tracing')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-ac-pseudo-arc',help='recompute branch equations, tangents and hyperplanes')
    cmd.add_argument('directory')

    cmd=sub.add_parser('ac-envelope',help='trace AC branch and bracket observed voltage, MVA and explicit pu-current limits')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-ac-envelope',help='recompute branch limits, observations and event brackets')
    cmd.add_argument('directory')

    cmd=sub.add_parser('ac-dispatch',help='bounded distributed active balancing and Q limits including former slack')
    cmd.add_argument('--input',required=True);cmd.add_argument('--out',required=True)
    cmd=sub.add_parser('verify-ac-dispatch',help='recompute distributed dispatch equations and allocation')
    cmd.add_argument('directory')

    args = parser.parse_args(argv)
    if args.command in {'ac-dispatch','verify-ac-dispatch'}:
        from power_grid_env.ac_dispatch import run_dispatch,verify_dispatch
        from power_grid_env.study_artifacts import read_json
        try:
            if args.command=='verify-ac-dispatch':public=verify_dispatch(args.directory)
            else:
                r=run_dispatch(read_json(args.input),args.out)
                public=dict(completed=r['completed'],reason=r['reason'],iterations=r['iterations'],audit=r['audit'],stability_proven=False)
            print(json.dumps(public,ensure_ascii=False));return 0 if public['completed'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError,KeyError,TypeError) as exc:
            print(json.dumps(dict(completed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'ac-envelope','verify-ac-envelope'}:
        from power_grid_env.ac_envelope import run_envelope,verify_envelope
        from power_grid_env.study_artifacts import read_json
        try:
            if args.command=='verify-ac-envelope':public=verify_envelope(args.directory)
            else:
                r=run_envelope(read_json(args.input),args.out);trace=r['trace'];report=r['envelope']
                public=dict(completed=bool(trace['completed'] and report['completed']),target_reached=trace['completed'],monitor_completed=report['completed'],
                    target_within_monitored_limits=report['target_within_monitored_limits'],monitored_limits=len(report['limits']),base_violations=len(report['base_violations']),observed_events=len(report['events']),p_control_changes=len(trace.get('p_control_changes',[])),q_control_changes=len(trace.get('control_changes',[])),timeline_complete=report['timeline_complete'],reason=report['reason'],trace_reason=trace['reason'],stability_proven=False)
            print(json.dumps(public,ensure_ascii=False));return 0 if public['completed'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError,KeyError,TypeError) as exc:
            print(json.dumps(dict(completed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'ac-pseudo-arc','verify-ac-pseudo-arc'}:
        from power_grid_env.ac_pseudo_arc import run_pseudo_arc,verify_pseudo_arc
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_pseudo_arc(args.directory) if args.command=='verify-ac-pseudo-arc' else run_pseudo_arc(read_json(args.input),args.out)
            public=r if args.command=='verify-ac-pseudo-arc' else dict(completed=r['completed'],reason=r['reason'],accepted_points=len(r['points']),sampled_turns=len(r['turns']),control_changes=len(r.get('control_changes',[])),p_control_changes=len(r.get('p_control_changes',[])),stability_proven=False,target_absence_proven=False)
            print(json.dumps(public,ensure_ascii=False));return 0 if r['completed'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError,KeyError,TypeError) as exc:
            print(json.dumps(dict(completed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'ac-continuation','verify-ac-continuation'}:
        from power_grid_env.ac_continuation import run_continuation,verify_continuation
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_continuation(args.directory) if args.command=='verify-ac-continuation' else run_continuation(read_json(args.input),args.out)
            public=r if args.command=='verify-ac-continuation' else {k:r[k] for k in ['completed','status','reason','accepted_progress','producer_sha256','target_loads_exact','scope']}
            if args.command=='ac-continuation':public['attempt_count']=len(r['attempts'])
            print(json.dumps(public,ensure_ascii=False));return 0 if r['completed'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError,KeyError,TypeError) as exc:
            print(json.dumps(dict(completed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'warm-flux-study','verify-warm-flux-study'}:
        from power_grid_env.warm_flux import run_warm_flux,verify_warm_flux
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_warm_flux(args.directory) if args.command=='verify-warm-flux-study' else run_warm_flux(read_json(args.input),args.out)
            print(json.dumps(r,ensure_ascii=False));return 0 if r['numerically_qualified'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError,KeyError,TypeError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'motor-transfer','verify-motor-transfer'}:
        from power_grid_env.motor_transfer import run_transfer,verify_transfer
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_transfer(args.directory) if args.command=='verify-motor-transfer' else run_transfer(read_json(args.input),args.out)
            print(json.dumps(r,ensure_ascii=False));return 0
        except (ValueError,OSError,ArithmeticError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command in {'motor-robustness','verify-motor-robustness'}:
        from power_grid_env.motor_robustness import run_robustness,verify_robustness
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_robustness(args.directory) if args.command=='verify-motor-robustness' else run_robustness(read_json(args.input),args.out)
            print(json.dumps(r,ensure_ascii=False));return 0 if r['all_points_qualified'] else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2

    if args.command=='verify-comparison':
        from power_grid_env.comparison import verify_comparison
        try:
            r=verify_comparison(args.directory);print(json.dumps(r,ensure_ascii=False));return 0 if r['required_comparisons_passed'] else 2
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command in {'motor-engineering','verify-motor-engineering'}:
        from power_grid_env.motor_engineering import run_engineering,verify_engineering
        from power_grid_env.study_artifacts import read_json
        try:
            r=verify_engineering(args.directory) if args.command=='verify-motor-engineering' else run_engineering(read_json(args.input),args.out)
            print(json.dumps(r,ensure_ascii=False));return 0 if r.get('verified') or r.get('validation_passed') else 2
        except (ValueError,OSError,RuntimeError,ArithmeticError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='llm-data':
        from power_grid_env.llm_training import generate_corpus,verify_corpus,export_public_dataset,CorpusVerifier,evaluate_predictions,_read_lines
        from power_grid_env.study_artifacts import read_json,encoded,seal
        try:
            if args.llm_data_action=='generate':
                r=generate_corpus(read_json(args.input),args.out);result=dict(counts=r['counts'],producer_sha256=r['producer_sha256'])
            elif args.llm_data_action=='verify':result=verify_corpus(args.directory)
            elif args.llm_data_action=='export':result=export_public_dataset(args.directory,args.out,split=args.split,kind=args.kind)
            elif args.llm_data_action=='evaluate':
                raw=_read_lines(args.input);result=evaluate_predictions(args.directory,raw,split=args.split)
                out=Path(args.out)
                if out.exists():raise ValueError('new evaluation result directory required')
                out.mkdir(parents=True);(out/'predictions.jsonl').write_bytes(b''.join(encoded(r)+b'\n' for r in raw));(out/'result.json').write_bytes(encoded(result));seal(out)
            else:
                raw=read_json(args.input)
                if not isinstance(raw,dict) or set(raw)!={'task_id','completions'}:raise ValueError('group input requires task_id and completions only')
                result=CorpusVerifier(args.directory,allowed_splits=(args.split,)).group(raw['task_id'],raw['completions'])
                out=Path(args.out)
                if out.exists():raise ValueError('new group result directory required')
                out.mkdir(parents=True);(out/'input.json').write_bytes(encoded(raw));(out/'result.json').write_bytes(encoded(result));seal(out)
            print(json.dumps(result,ensure_ascii=False));return 2 if result.get('status') in {'discard_simulation_failure','unqualified_simulation_failure'} else 0
        except (ValueError,OSError,RuntimeError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='anchor-dynamic-motor':
        from power_grid_env.stator_resistance_anchor import run_anchored_identification
        from power_grid_env.study_artifacts import read_json
        try:
            result=run_anchored_identification(read_json(args.input),args.out)
            print(json.dumps({k:result.get(k) for k in ['completed','passed','reason','rhs_evaluations']}));return 0 if result['passed'] else 2
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='calibrated-motor':
        from power_grid_env.calibrated_motor_study import run_calibrated_study
        try:
            result=run_calibrated_study(args.input,args.out)
            print(json.dumps({k:result.get(k) for k in ['completed','passed','reason']}));return 0 if result['passed'] else 2
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='audit-dynamic-motor':
        from power_grid_env.dynamic_identification_audit import run_audit_artifact
        from power_grid_env.study_artifacts import read_json
        try:
            result=run_audit_artifact(read_json(args.input),args.out)
            print(json.dumps({k:result.get(k) for k in ['completed','passed','fit_passed','raw_residuals_passed','reason']}));return 0 if result['passed'] else 2
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='motor-recordings':
        from power_grid_env.motor_recordings import import_recordings,verify_recordings
        try:
            result=import_recordings(args.input,args.out) if args.recording_action=='import' else verify_recordings(args.directory)
            print(json.dumps(result,ensure_ascii=False));return 0
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(prepared=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='identify-dynamic-motor':
        from power_grid_env.advanced_studies import run_dynamic_identification_artifact
        from power_grid_env.study_artifacts import read_json
        try:
            if args.recordings:
                from power_grid_env.motor_recordings import load_recordings
                document=load_recordings(args.recordings)
            else:document=read_json(args.input)
            result=run_dynamic_identification_artifact(document,args.out)
            print(json.dumps({k:result.get(k) for k in ['completed','passed','identifiable','reason']}));return 0 if result['passed'] else 2
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command=='campaign':
        from power_grid_env.campaign import expand_grid,run_campaign,verify_campaign
        from power_grid_env.study_artifacts import read_json,verify,export_zip,extract_zip
        try:
            action=args.campaign_action
            if action=='run':
                result=run_campaign(read_json(args.plan),args.out,resume_from=args.resume_from,recover_from=args.recover_from,cancel_file=args.cancel_file)
                print(json.dumps({k:result[k] for k in ['id','completed','passed','halt_reason']}));return 0 if result['passed'] else 2
            if action=='grid':
                plan=expand_grid(read_json(args.input))
                with Path(args.out).open('x',encoding='utf-8') as stream:stream.write(plan.model_dump_json(indent=2)+'\n')
                result=dict(cases=len(plan.cases),id=plan.id)
            elif action=='verify':result=dict(manifest_sha256=verify_campaign(Path(args.source)))
            elif action=='export':
                verify_campaign(Path(args.source));result=export_zip(Path(args.source),Path(args.out))
            else:
                target=extract_zip(Path(args.source),Path(args.out))
                try:verify_campaign(target)
                except (ValueError,KeyError,OSError):
                    import shutil
                    shutil.rmtree(target);raise
                result=dict(directory=str(target))
            print(json.dumps(result));return 0
        except (ValueError,OSError,KeyError) as exc:
            print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False));return 2
    if args.command in {'protected-motor','sensitivity-study'}:
        from power_grid_env.study_artifacts import read_json
        from power_grid_env.advanced_studies import run_protected_artifact,run_sensitivity_artifact
        try:
            if args.command=='protected-motor' and args.catalog:
                from power_grid_env.motor_catalog import protected_catalog_study
                document=protected_catalog_study(args.catalog,args.mode).model_dump(mode='json')
            else:document=read_json(args.study)
            result=run_protected_artifact(document,args.out) if args.command=='protected-motor' else run_sensitivity_artifact(document,read_json(args.protocol),args.out)
            print(json.dumps({k:result[k] for k in ['completed','available','engineering_acceptable','status','reason'] if k in result},ensure_ascii=False,allow_nan=False))
            return 0 if result.get('completed') and result.get('available',True) and (args.command!='protected-motor' or not args.require_acceptable or result.get('engineering_acceptable',False)) else 2
        except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
            print(json.dumps(dict(completed=False,status='rejected',error=str(exc)),ensure_ascii=False));return 2
    if args.command == 'phase-study':
        from power_grid_env.study_artifacts import read_json, verify, export_zip
        from power_grid_env.phase_research import run_phase_study, run_search, train_from_search, query_model
        try:
            if args.phase_command == 'initialize':
                from power_grid_env.phase_network import periodic_initialization
                from power_grid_env.phase_research import PhaseStudy, EmtOptions
                from power_grid_env.study_artifacts import encoded
                original = PhaseStudy.model_validate(read_json(args.study))
                if original.mode != 'harmonic': raise ValueError('periodic initialization requires a harmonic study')
                prepared = PhaseStudy(id=original.id+'-periodic-emt', case=periodic_initialization(original.case), mode='emt', emt=EmtOptions(duration_s=args.duration, sample_s=args.sample, max_step_s=args.max_step))
                target = Path(args.out)
                if target.exists(): raise ValueError('new initialized study output required')
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream: stream.write(encoded(prepared.model_dump(mode='json')))
                result = dict(completed=True, study=str(target))
            elif args.phase_command == 'run': result = run_phase_study(read_json(args.study), args.out)
            elif args.phase_command == 'search': result = run_search(read_json(args.protocol), args.out, max_points=args.max_points, resume_from=args.resume_from, cancel_file=args.cancel_file)
            elif args.phase_command == 'train': result = train_from_search(args.directory, args.out, maximum_holdout_error=args.max_error, degree=args.degree, seed=args.seed)
            elif args.phase_command == 'predict': result = query_model(args.directory, json.loads(args.point), args.out, screen_only=args.screen_only)
            elif args.phase_command == 'verify': result = dict(integrity_verified=True, manifest_sha256=verify(args.directory))
            else: result = export_zip(args.directory, args.out)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 2 if result.get('completed') is False or result.get('accepted') is False or result.get('conclusive') is False else 0
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            print(json.dumps(dict(completed=False, status='rejected', error=str(exc)), ensure_ascii=False)); return 2
    if args.command == 'steady-study':
        from power_grid_env.study_artifacts import read_json, encoded, export_zip, verify
        from power_grid_env.steady_study import run_steady_study
        try:
            if args.steady_command == 'import-matpower':
                from power_grid_env.matpower_case import load_case
                from power_grid_env.balanced_ac import case_document
                path = Path(args.out)
                if path.exists(): raise ValueError('new import output required')
                data = encoded(case_document(load_case(args.source)))
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream: stream.write(data)
                result = dict(completed=True, case=str(path), profile='balanced MATPOWER literal numeric matrices; no unbalanced Network conversion')
            elif args.steady_command == 'derive':
                from power_grid_env.steady_study import derive_dispatch_study
                path = Path(args.out)
                if path.exists(): raise ValueError('new derived study output required')
                payload = encoded(derive_dispatch_study(args.directory, args.task))
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream: stream.write(payload)
                result = dict(completed=True, study=str(path))
            elif args.steady_command == 'run':
                result = run_steady_study(read_json(args.study), args.out, resume_from=args.resume_from,
                    max_cases=args.max_cases, cancel_file=args.cancel_file, time_budget_s=args.time_budget)
            elif args.steady_command == 'verify':
                result = dict(integrity_verified=True, manifest_sha256=verify(args.directory))
            else:
                result = export_zip(args.directory, args.out)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0 if result.get('completed', True) else 2
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            print(json.dumps(dict(completed=False, status='rejected', error=str(exc)), ensure_ascii=False)); return 2
    if args.command=='workbench':
        from power_grid_env.engineering_project import EngineeringProject,import_engineering_project
        from power_grid_env.study_artifacts import read_json
        from power_grid_env.study_spec import StudySpec
        try:
            name=args.workbench_command;project=EngineeringProject(args.dir)
            if name=='create':
                project=EngineeringProject.create(args.dir,StudySpec.model_validate(read_json(args.study)));result=project.check()
            elif name=='revise':result=dict(revision=project.revise(StudySpec.model_validate(read_json(args.study))))
            elif name=='import':result=import_engineering_project(args.source,args.dir,sidecar=args.sidecar)
            elif name=='check':result=project.check(args.revision)
            elif name=='status':result=project.descriptor()
            elif name=='cancel':project.request_cancel();result=dict(cancel_requested=True)
            elif name=='clear-cancel':project.clear_cancel();result=dict(cancel_requested=False)
            elif name=='run':result=project.run(args.run_id,revision=args.revision,resume_from=args.resume_from,max_steps=args.max_steps,timeout_s=args.timeout,checkpoint_every=args.checkpoint_every)
            elif name=='verify':
                path,meta=project.verify_run(args.run_id);result=dict(integrity_verified=True,run=meta)
            elif name=='export':result=project.export(args.run_id,args.out)
            elif name=='restore':project=EngineeringProject.from_export(args.archive,args.dir);result=project.descriptor()
            elif name=='compare':
                from power_grid_env.engineering_report import compare_runs
                if Path(args.out).resolve().is_relative_to(project.root) or Path(args.out).exists():raise ValueError('new comparison output outside project required')
                left,_=project.verify_run(args.baseline);right,_=project.verify_run(args.candidate)
                result=compare_runs(left,right,absolute=args.absolute,relative=args.relative,time_absolute_s=args.time_tolerance);write_json(Path(args.out),result)
            print(json.dumps(result,ensure_ascii=False))
            return 2 if result.get('ok') is False or result.get('passed') is False or result.get('completed') is False else 0
        except (ValueError,OSError,KeyError,RuntimeError) as exc:
            print(json.dumps(dict(completed=False,status='rejected',error=str(exc)),ensure_ascii=False));return 2
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
    if args.command=='identify-motor':
        from power_grid_env.motor_identification import MotorIdentification,identify
        try:
            path=Path(args.observations)
            if path.stat().st_size>16000000:raise ValueError('motor identification input byte budget')
            spec=MotorIdentification.model_validate_json(path.read_text());result=identify(spec)
        except (ValueError,OSError) as exc:result=dict(completed=False,passed=False,status='rejected',detail=str(exc))
        write_json(Path(args.out),result);print(json.dumps(dict(completed=result['completed'],passed=result['passed'])))
        return 0 if result['passed'] else 2
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
    if args.command == "motor-bundle":
        if args.bundle_command!="create" and Path(args.out).resolve().is_relative_to(Path(args.bundle).resolve()):
            print(json.dumps(dict(completed=False,status='rejected',error='replay/verification output must be outside sealed bundle')))
            return 2
        from power_grid_env.motor_bundle import MotorReplayCriteria,export_motor_bundle,verify_motor_bundle,replay_motor_bundle
        from power_grid_env.motor_trace import MotorTraceProtocol
        from power_grid_env.study_spec import StudySpec
        try:
            if args.bundle_command=="create":
                spec=StudySpec.model_validate_json(Path(args.study).read_text())
                criteria=MotorReplayCriteria.model_validate_json(Path(args.criteria).read_text())
                protocol=MotorTraceProtocol.model_validate_json(Path(args.trace_protocol).read_text()) if args.trace_protocol else None
                result=export_motor_bundle(spec,criteria,args.out,trace=args.trace,trace_protocol=protocol,
                    cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
            elif args.bundle_command=="verify":
                _,_,_,comparison,_=verify_motor_bundle(args.bundle)
                result=dict(completed=True,status='verified',measurement_comparison_passed=None if comparison is None else comparison['passed'],
                    provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)
            else:
                result=replay_motor_bundle(args.bundle,cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            result=dict(completed=False,status='rejected',error=str(exc),industrial_profile_qualified=False,field_validated=False)
        if args.bundle_command!="create":write_json(Path(args.out),result)
        print(json.dumps({k:v for k,v in result.items() if k!='metrics'},ensure_ascii=False))
        return 0 if result['completed'] and result.get('repeatable',True) and result.get('measurement_comparison_passed') is not False else 2

    if args.command == "fault-clearing":
        from power_grid_env.fault_clearing import FaultClearingProtocol,run_fault_clearing
        from power_grid_env.study_spec import StudySpec
        try:
            spec=StudySpec.model_validate_json(Path(args.study).read_text())
            protocol=FaultClearingProtocol.model_validate_json(Path(args.protocol).read_text())
            result=run_fault_clearing(spec,protocol,args.out,
                cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (OSError,ValueError,TypeError) as exc:
            result=dict(completed=False,all_cases_within_limits=False,status='rejected',error=str(exc))
            print(str(exc)[:4000],file=sys.stderr)
        print(json.dumps({k:result[k] for k in ('completed','all_cases_within_limits','status')},ensure_ascii=False))
        if result['status'] in {'cancelled','total_timeout'}:return 3
        return 0 if result['completed'] and result['all_cases_within_limits'] else 2

    if args.command in {'motor-restart-check','motor-restart-replay'}:
        from power_grid_env.motor_restart_audit import verify_motor_restart,replay_motor_restart
        try:
            if args.command=='motor-restart-check':
                root=Path(args.directory).resolve();target=Path(args.out).resolve()
                if target==root or target.is_relative_to(root):raise ValueError('verification output must be outside original experiment')
                result=verify_motor_restart(Path(args.directory));write_json(target,result)
                print(json.dumps(result,ensure_ascii=False));return 0
            result=replay_motor_restart(Path(args.directory),Path(args.out),cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
            print(json.dumps({k:result[k] for k in ['completed','repeatable','status']},ensure_ascii=False))
            if result['status'] in {'cancelled','total_timeout'}:return 3
            return 0 if result['completed'] and result['repeatable'] else 2
        except (OSError,ValueError,TypeError,KeyError) as exc:
            print(str(exc)[:4000],file=sys.stderr);return 2

    if args.command == "motor-restart":
        from power_grid_env.motor_restart import MotorRestartProtocol,run_motor_restart
        from power_grid_env.study_spec import StudySpec
        try:
            result=run_motor_restart(StudySpec.model_validate_json(Path(args.study).read_text()),MotorRestartProtocol.model_validate_json(Path(args.protocol).read_text()),Path(args.out),cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (OSError,ValueError,TypeError) as exc:
            result=dict(completed=False,status='rejected',candidates_within_sampled_limits=[],engineering_assessment='inconclusive',error=str(exc))
            print(str(exc)[:4000],file=sys.stderr)
        print(json.dumps({k:result.get(k) for k in ['completed','status','candidates_within_sampled_limits','engineering_assessment']},ensure_ascii=False))
        if result['status'] in {'cancelled','total_timeout'}:return 3
        return 0 if result['completed'] and result.get('candidates_within_sampled_limits') else 2

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

    if args.command=='critical-modes':
        from power_grid_env.study_spec import StudySpec
        from power_grid_env.dae_spectrum import critical_modes
        try:
            path=Path(args.study)
            if path.stat().st_size>32000000:raise ValueError('spectral study input byte budget')
            spec=StudySpec.model_validate_json(path.read_text())
            if spec.study=='coupled_rms':
                from power_grid_env.coupled_rms import CoupledNetwork,validate_coupled
                from power_grid_env.coupled_initialization import initialize_coupled
                if not validate_coupled(spec.network,spec.coupled)['ok']:raise ValueError('unsupported spectral network scope')
                model=CoupledNetwork(spec.network,spec.coupled);state,initial=initialize_coupled(model,require_stable=False)
            elif spec.study=='motor_start':
                from power_grid_env.induction_network import InductionNetwork,validate_induction
                from power_grid_env.motor_operating_point import initialize_motor_state
                if not validate_induction(spec.network,spec.induction)['ok']:raise ValueError('unsupported spectral network scope')
                model=InductionNetwork(spec.network,spec.induction);state,initial=initialize_motor_state(model)
            else:raise ValueError('critical-modes requires motor_start or coupled_rms')
            if state is None:raise ValueError('spectral initialization failed: '+str(initial))
            indices=None if args.indices is None else [int(x) for x in args.indices.split(',')]
            result=critical_modes(model,state,indices=indices,count=args.count)
        except (ValueError,OSError,RuntimeError) as exc:
            result=dict(completed=False,status='failed',error=str(exc))
        write_json(Path(args.out),result);print(json.dumps(dict(completed=result['completed'])));return 0 if result['completed'] else 2

    if args.command=='industrial-dol':
        from power_grid_env.industrial_dol import IndustrialDOLPack,run_pack
        try:
            path=Path(args.pack)
            if path.stat().st_size>32000000:raise ValueError('industrial DOL input byte budget')
            pack=IndustrialDOLPack.model_validate_json(path.read_text())
            result=run_pack(pack,Path(args.out),cancelled=(lambda:Path(args.cancel_file).exists()) if args.cancel_file else None)
        except (ValueError,OSError,RuntimeError) as exc:
            print(json.dumps(dict(completed=False,status='rejected',error=str(exc)),ensure_ascii=False));return 2
        print(json.dumps({k:result[k] for k in ['completed','purpose','validation_passed','engineering_acceptable']},ensure_ascii=False))
        return 0 if result['validation_passed'] and (pack.purpose=='regression' or result['engineering_acceptable']) else 2

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

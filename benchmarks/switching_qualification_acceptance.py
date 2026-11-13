"""Conformance of the public bounded temporal-qualification workflow.

A passing harness includes the expected rejection of the unresolved double-cage
profile; it never means every physical study is temporally qualified.
"""
import argparse
import hashlib
from pathlib import Path
import tempfile
from benchmarks.extended_closed_loop_acceptance import NAMES,document,producer
from benchmarks.numerical_replay import assert_replay_equal
from power_grid_env.switching_convergence import run_switching_qualification_artifact
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


def identity():
    seed=Path(__file__).resolve().parents[1]/'cases/studies/flux-saturated_dol.json'
    return dict(producer=producer(),benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),seed_sha256=hashlib.sha256(seed.read_bytes()).hexdigest())


def run(out,replay=False):
    out=Path(out);before=identity()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('qualification workflow producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    rows=[]
    with tempfile.TemporaryDirectory(prefix='pge-qualification-replay-') as temporary:
        for name in NAMES:
            target=Path(temporary)/name if replay else out/name
            result=run_switching_qualification_artifact(document(name),target,max_levels=6)
            if replay:
                verify(target);verify(out/name)
                paths={p.relative_to(target) for p in target.rglob('*.json') if p.name!='manifest.json'}
                if paths!={p.relative_to(out/name) for p in (out/name).rglob('*.json') if p.name!='manifest.json'}:raise ValueError('qualification replay file inventory differs')
                for relative in paths:
                    if relative.name in {'study.json','protocol.json'}:
                        if (target/relative).read_bytes()!=(out/name/relative).read_bytes():raise ValueError('qualification replay input differs')
                    else:assert_replay_equal(read_json(out/name/relative),read_json(target/relative))
            expected=name!='double_cage'
            audits=all(all(a['physics_audits'].values()) for a in result['attempts'] if a['status']=='completed')
            healthy=result['completed'] and all(a['status']=='completed' for a in result['attempts']) and audits
            row=dict(case=name,workflow_passed=bool(healthy and result['qualified']==expected),qualified=result['qualified'],expected_qualified=expected,status=result['status'],selected_step_s=result['selected_step_s'],trajectories=sum(a['status']=='completed' for a in result['attempts']),all_attempts_retained=True)
            rows.append(row);print(encoded(row).decode(),flush=True)
    summary=dict(schema='power-grid-env.switching-qualification-workflow/v1',passed=all(r['workflow_passed'] for r in rows),all_cases_temporally_qualified=all(r['qualified'] for r in rows),trajectories=sum(r['trajectories'] for r in rows),rows=rows,scope='six-level public API qualification workflow, including mandatory rejection of unresolved double-cage study; no independent oracle, absolute error certification or external ranking',superiority_established=False)
    if replay:assert_replay_equal(read_json(out/'summary.json'),summary)
    else:(out/'summary.json').write_bytes(encoded(summary))
    if identity()!=before:raise ValueError('qualification workflow producer changed')
    receipt=verify(out) if replay else seal(out)
    if replay and receipt!=manifest:raise ValueError('qualification artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],all_cases_temporally_qualified=summary['all_cases_temporally_qualified'],replayed=replay,manifest_sha256=receipt)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');args=parser.parse_args();run(args.verify or args.out,bool(args.verify))

"""Local shared-case PF, OPF, AC estimation and immutable N−1 attempts.

N−1 interruption is cooperative between bounded Newton solves. A completed
assessment is not the same as a secure network or a successful redispatch.
"""
from dataclasses import replace
import hashlib
import math
import os
from pathlib import Path
import time

from power_grid_env.balanced_ac import case_from_document, case_document, solve_power_flow, branch_results
from power_grid_env.ac_estimation import Measurement, estimate_state
from power_grid_env.evidence import package_fingerprint
from power_grid_env.network_opf import solve_ac_opf
from power_grid_env.report import write_json
from power_grid_env.study_artifacts import encoded, digest, read_json, seal, verify, MAX_FILE

SCHEMA = 'power-grid-env.steady-study/v1'


def validate_study(document):
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise ValueError('steady study schema required')
    if set(document) - {'schema', 'id', 'task', 'case', 'measurements', 'options', 'branch_indices', 'derived_from'}:
        raise ValueError('unknown steady study fields')
    if not isinstance(document.get('id'), str) or not document['id']:
        raise ValueError('study ID required')
    case = case_from_document(document['case'])
    task = document.get('task')
    allowed = {'power_flow': {'tolerance_pu', 'max_iterations', 'enforce_q_limits'}, 'n1': {'tolerance_pu', 'max_iterations', 'enforce_q_limits'},
               'opf': set(), 'state_estimation': {'max_evaluations', 'reject_bad_data', 'normalized_threshold', 'significance', 'max_removals'}}
    if task not in allowed or not isinstance(document.get('options', {}), dict) or set(document.get('options', {}))-allowed[task]:
        raise ValueError('unsupported steady study task/options')
    if task != 'state_estimation' and document.get('measurements'):
        raise ValueError('measurements are only used by state_estimation')
    if task != 'n1' and 'branch_indices' in document:
        raise ValueError('branch selection is N-1-only')
    if task == 'n1':
        chosen = document.get('branch_indices', [i for i, b in enumerate(case.branches) if b.status])
        if not isinstance(chosen, list) or not chosen or len(chosen) != len(set(chosen)) or any(type(i) is not int or not 0 <= i < len(case.branches) or not case.branches[i].status for i in chosen):
            raise ValueError('N-1 requires unique active branch indices')
    if task == 'state_estimation':
        try:
            rows = [Measurement(**row) for row in document.get('measurements', [])]
        except TypeError as exc:
            raise ValueError('invalid measurement fields') from exc
        from power_grid_env.ac_estimation import MeasurementModel
        MeasurementModel(case, rows)
    if len(encoded(document)) > MAX_FILE:
        raise ValueError('study byte budget exceeded')
    return case


def run_steady_study(document, out, *, resume_from=None, max_cases=None, cancel_file=None, time_budget_s=None):
    case = validate_study(document)
    if max_cases is not None and (type(max_cases) is not int or max_cases < 1):
        raise ValueError('positive case budget required')
    if time_budget_s is not None and (not math.isfinite(time_budget_s) or not 0 < time_budget_s <= 3600):
        raise ValueError('invalid cooperative time budget')
    task = document['task']; options = document.get('options', {})
    if task != 'n1' and time_budget_s is not None:
        raise ValueError('cooperative time budget is N-1-only')
    if task != 'n1' and (resume_from is not None or max_cases is not None):
        raise ValueError('resume and max_cases are N-1-only')
    out = Path(out)
    if out.exists():
        raise ValueError('new output directory required; attempts are immutable')
    identity = digest(encoded(document)); producer = package_fingerprint()
    parent = None
    if resume_from is not None:
        parent = Path(resume_from)
        if out.resolve().is_relative_to(parent.resolve()) or parent.resolve().is_relative_to(out.resolve()):
            raise ValueError('resume attempt and parent must be disjoint')
        verify(parent)
        checkpoint = read_json(parent/'checkpoint.json')
        if checkpoint['study_sha256'] != identity or checkpoint['code_sha256'] != producer:
            raise ValueError('resume study or producer differs')
        if read_json(parent/'result.json')['status'] not in {'paused', 'cancelled', 'time_budget_exhausted', 'artifact_budget_exhausted'}:
            raise ValueError('parent attempt is not resumable')
    out.mkdir(parents=True)
    write_json(out/'study.json', document)
    started = time.monotonic()
    cancelled = lambda: cancel_file is not None and Path(cancel_file).exists()
    if task != 'n1':
        if cancelled():
            result = dict(completed=False, status='cancelled')
        elif task == 'power_flow':
            result = solve_power_flow(case, **options)
        elif task == 'opf':
            result = solve_ac_opf(case)
            result['completed'] = result['success']
            if result['success']:
                import numpy as np
                result['branches'] = branch_results(case, result['vm_pu'], np.radians(result['va_deg']))
        else:
            result = estimate_state(case, [Measurement(**row) for row in document['measurements']], **options)
    else:
        result = _run_n1(case, document, options, out, parent, identity, producer, max_cases, cancelled, started, time_budget_s)
    if package_fingerprint() != producer:
        raise ValueError('producer changed during study; attempt left unsealed')
    result.update(study_sha256=identity, code_sha256=producer, study_id=document['id'], task=task,
                  elapsed_s=time.monotonic()-started, policy='local_only_no_GitHub_no_CI')
    encoded(result)  # Refuse nonfinite diagnostics before sealing or reporting success.
    write_json(out/'result.json', result)
    manifest = seal(out)
    return dict(**result, artifact_manifest_sha256=manifest)


def _run_n1(case, document, options, out, parent, identity, producer, max_cases, cancelled, started, budget):
    chosen = sorted(document.get('branch_indices', [i for i, b in enumerate(case.branches) if b.status]))
    journal = out/'cases.jsonl'; count = 0; state = hashlib.sha256(); counts = {}; violations = 0
    if parent is None:
        base = solve_power_flow(case, **options); write_json(out/'base.json', base); journal.touch()
    else:
        checkpoint = read_json(parent/'checkpoint.json'); raw = (parent/'cases.jsonl').read_bytes()
        size = checkpoint['journal_bytes']; count = checkpoint['completed_cases']
        if type(size) is not int or not 0 <= size <= len(raw) or type(count) is not int or not 0 <= count <= len(chosen):
            raise ValueError('invalid N-1 checkpoint position')
        raw = raw[:size]
        if digest(raw) != checkpoint['journal_sha256'] or raw.count(b'\n') != count:
            raise ValueError('N-1 committed journal mismatch')
        rows = [__import__('json').loads(line) for line in raw.splitlines()]
        if [r['branch_index'] for r in rows] != chosen[:count]:
            raise ValueError('N-1 checkpoint order differs')
        for row in rows:
            status = row['result']['status']; counts[status] = counts.get(status, 0)+1
            violations += not row['result'].get('within_limits', False)
        journal.write_bytes(raw); state.update(raw)
        base = read_json(parent/'base.json'); write_json(out/'base.json', base)
    def commit(stream):
        stream.flush(); os.fsync(stream.fileno())
        write_json(out/'checkpoint.json', dict(study_sha256=identity, code_sha256=producer,
            completed_cases=count, journal_bytes=stream.tell(), journal_sha256=state.hexdigest()))
        with (out/'checkpoint.json').open('rb') as saved:
            os.fsync(saved.fileno())
        directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    status = 'completed'; performed = 0
    with journal.open('ab') as stream:
        commit(stream)
        for branch_index in chosen[count:]:
            if cancelled():
                status = 'cancelled'; break
            if max_cases is not None and performed >= max_cases:
                status = 'paused'; break
            if budget is not None and time.monotonic()-started >= budget:
                status = 'time_budget_exhausted'; break
            branches = [replace(b, status=0) if i == branch_index else b for i, b in enumerate(case.branches)]
            trial = replace(case, branches=branches)
            solved = solve_power_flow(trial, **options)
            row = encoded(dict(branch_index=branch_index, result=solved))+b'\n'
            if stream.tell()+len(row) > MAX_FILE-1_000_000:
                status = 'artifact_budget_exhausted'; break
            stream.write(row); state.update(row); count += 1; performed += 1
            counts[solved['status']] = counts.get(solved['status'], 0)+1
            violations += not solved.get('within_limits', False)
            commit(stream)
    return dict(completed=count == len(chosen), status=status, evaluated_cases=count, total_cases=len(chosen),
                excluded_offline_branch_indices=[i for i, b in enumerate(case.branches) if not b.status],
                status_counts=counts, failed_or_limit_violating_cases=violations,
                base_converged=base['converged'], base_within_limits=base.get('within_limits', False),
                secure=bool(count == len(chosen) and not violations and base.get('within_limits', False)),
                journal_sha256=state.hexdigest(), redispatch_applied=False,
                scope='static branch N-1, fixed non-reference Pg, PV Q-limit switching; islands require an explicit reference policy')


def derive_dispatch_study(directory, task):
    from power_grid_env.balanced_ac import dispatch_case
    directory = Path(directory)
    manifest = verify(directory)
    source = read_json(directory/'study.json'); result = read_json(directory/'result.json')
    case = validate_study(source)
    if task not in {'power_flow', 'n1'} or source['task'] != 'opf':
        raise ValueError('derive requires an OPF parent and PF/N-1 target')
    if result['study_sha256'] != digest(encoded(source)) or result['task'] != 'opf':
        raise ValueError('OPF artifact input/result identity differs')
    derived = dict(schema=SCHEMA, id=source['id']+'-'+task, task=task,
                   case=case_document(dispatch_case(case, result)),
                   derived_from=dict(manifest_sha256=manifest, study_sha256=result['study_sha256'], code_sha256=result['code_sha256']))
    validate_study(derived)
    return derived

"""Killable RMS execution in a trusted local worker; not an OS sandbox."""
from __future__ import annotations
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from power_grid_env.evidence import package_fingerprint
from power_grid_env.ipc import kill_process_group
from power_grid_env.report import write_json
from power_grid_env.model import canonical_hash


def run_isolated_rms(spec, *, timeout_s=120., cancelled=None, max_result_bytes=64_000_000, max_log_bytes=1_000_000):
    if not math.isfinite(timeout_s) or timeout_s<=0 or max_result_bytes<1 or max_log_bytes<1:
        raise ValueError('positive finite deadline and output budgets required')
    if cancelled is not None and cancelled():
        return _failure('cancelled',0.)
    code=package_fingerprint()
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='pge-rms-worker-') as directory:
        root=Path(directory)
        write_json(root/'study.json',spec.model_dump(mode='json'))
        def over_budget():
            return any((root/name).exists() and (root/name).stat().st_size>limit for name,limit in
                       [('stdout.log',max_log_bytes),('stderr.log',max_log_bytes),
                        ('result.json',max_result_bytes),('result.json.tmp',max_result_bytes)])
        with (root/'stdout.log').open('wb') as stdout,(root/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,'-m','power_grid_env.rms_worker',str(root/'study.json'),str(root/'result.json')],
                                     stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
            reason=None
            try:
                while process.poll() is None:
                    if cancelled is not None and cancelled():reason='cancelled'
                    elif time.monotonic()-started>=timeout_s:reason='timeout'
                    elif over_budget():reason='worker_output_limit'
                    if reason:
                        kill_process_group(process);break
                    time.sleep(.01)
                elapsed=time.monotonic()-started
                if reason:return _failure(reason,elapsed)
                if elapsed>=timeout_s:return _failure('timeout',elapsed)
                if over_budget():return _failure('worker_output_limit',elapsed)
                if process.returncode!=0 or not (root/'result.json').exists():
                    report=_failure('worker_failed',elapsed)
                    report['stderr_tail']=(root/'stderr.log').read_text(errors='replace')[-4000:]
                    return report
                try:
                    report=json.loads((root/'result.json').read_text())
                except (OSError,ValueError):
                    return _failure('invalid_worker_result',elapsed)
                if not isinstance(report,dict) or type(report.get('completed')) is not bool:
                    return _failure('invalid_worker_result',elapsed)
                if report['completed'] and (report.get('code_sha256')!=code or package_fingerprint()!=code):
                    return _failure('stale_worker_code',elapsed)
                if report['completed'] and (report.get('network_sha256')!=canonical_hash(spec.network)
                        or report.get('settings')!=(spec.coupled if spec.study=='coupled_rms' else spec.induction if spec.study=='motor_start' else spec.rms).model_dump(mode='json')):
                    return _failure('mismatched_worker_result',elapsed)
                report['execution']={'mode':'isolated_process','elapsed_s':elapsed,'timeout_s':timeout_s,
                                     'max_result_bytes':max_result_bytes,'os_sandbox':False}
                return report
            finally:
                if process.poll() is None:kill_process_group(process)


def _failure(status,elapsed):
    return {'solved':False,'completed':False,'status':status,
            'execution':{'mode':'isolated_process','elapsed_s':elapsed,'os_sandbox':False}}

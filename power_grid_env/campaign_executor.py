"""FIFO local process scheduler with bounded concurrent reservations.

No shared solver state, network service or distributed execution. Results and
case identities use the same format for sequential and parallel execution.
"""
from pathlib import Path
import os,signal,subprocess,sys,time,shutil,stat
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify,MAX_FILE,MAX_FILES
from power_grid_env.report import write_json
from power_grid_env.evidence import package_fingerprint
from power_grid_env.paths import package_dir


def stop_worker(process):
    """Always reap, including exit between poll() and signal delivery."""
    if process.poll() is None:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    return process.wait()


def output_exceeded(root):
    sizes=[]
    for path in root.rglob('*'):
        try:entry=path.stat()
        except FileNotFoundError:continue  # Atomic writer renamed a temporary.
        if stat.S_ISREG(entry.st_mode):sizes.append(entry.st_size)
    return len(sizes)>MAX_FILES-8 or sum(sizes)>192_000_000 or max(sizes,default=0)>MAX_FILE


def reusable(plan,code,source,recover):
    from power_grid_env.campaign import verify_campaign,validate_attempt
    if source is None:return None,{},None
    parent=Path(source).resolve();before=None
    if not recover:
        before=verify_campaign(parent);previous=read_json(parent/'summary.json')
        if previous['code_sha256']!=code:raise ValueError('continuation plan or producer differs')
    if digest(encoded(read_json(parent/'plan.json')))!=digest(encoded(plan.model_dump(mode='json'))):raise ValueError('continuation plan or producer differs')
    prior={}
    for case in plan.cases:
        path=parent/'attempts'/case.id
        if not (path/'manifest.json').is_file():continue
        row=validate_attempt(path,case,code)
        if row['assessment']['numerically_completed'] and row['assessment']['available']:prior[case.id]=row
    return parent,prior,before


def execute(document,out,*,resume_from=None,recover_from=None,cancel_file=None):
    from power_grid_env.campaign import CampaignPlan,_identity,assessment,_report,verify_campaign
    plan=CampaignPlan.model_validate(document);payload=plan.model_dump(mode='json');code=package_fingerprint()
    out=Path(out).absolute()
    if out.exists():raise ValueError('new campaign directory required')
    if resume_from is not None and recover_from is not None:raise ValueError('choose resume or crash recovery')
    source=resume_from if resume_from is not None else recover_from
    if source is not None and out.resolve().is_relative_to(Path(source).resolve()):raise ValueError('new campaign must be outside its parent')
    parent,prior,parent_manifest=reusable(plan,code,source,recover_from is not None)
    out.mkdir(parents=True);write_json(out/'plan.json',payload)
    started=time.monotonic();active={};finished={};cursor=0;halt=None;audit=[];peak_workers=0;peak_memory=0

    def note(kind,case,**details):
        audit.append(dict(kind=kind,case_id=case.id,elapsed_s=time.monotonic()-started,**details))

    def finish(job,forced=None):
        case=job['case'];target=job['path'];process=job['process']
        if forced:stop_worker(process)
        else:process.wait()
        job['stdout'].close();job['stderr'].close()
        status=forced or ('finished' if process.returncode==0 else 'worker_error')
        check=dict(numerically_completed=False,available=False,passed=False,rules=[])
        if status=='finished':
            try:
                identity=read_json(target/'worker.json')
                if not isinstance(identity,dict) or identity.get('code_sha256')!=code or identity.get('case_sha256')!=_identity(case):raise ValueError('worker identity differs')
                check=assessment(case,read_json(target/'result.json'))
                status='passed' if check['passed'] else 'criteria_failed' if check['numerically_completed'] else 'calculation_failed'
            except (ValueError,KeyError,OSError,TypeError):status='invalid_worker_result'
        if status=='output_budget_exceeded':
            for p in target.rglob('*'):
                if p.is_file() and p.name!='case.json':p.unlink()
        row=dict(id=case.id,status=status,passed=check['passed'],assessment=check,case_sha256=_identity(case),code_sha256=code,elapsed_s=time.monotonic()-job['started'],worker_exit_code=process.returncode,parameters=case.parameters,reused=False)
        write_json(target/'execution.json',row);row['attempt_manifest_sha256']=seal(target);finished[case.id]=row
        note('finished',case,status=status)

    def launch(case):
        target=out/'attempts'/case.id;target.mkdir(parents=True);write_json(target/'case.json',case.model_dump(mode='json'))
        env=os.environ.copy();env.update(PYTHONPATH=str(package_dir().parent),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
        stdout=(target/'stdout.log').open('wb')
        try:stderr=(target/'stderr.log').open('wb')
        except BaseException:stdout.close();raise
        begin=time.monotonic()
        try:process=subprocess.Popen([sys.executable,'-m','power_grid_env.campaign_worker',str(target)],cwd=target,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
        except OSError as exc:
            stdout.close();stderr.close();write_json(target/'launcher-error.json',dict(error=str(exc)))
            row=dict(id=case.id,status='worker_start_error',passed=False,assessment=dict(numerically_completed=False,available=False,passed=False,rules=[]),case_sha256=_identity(case),code_sha256=code,elapsed_s=time.monotonic()-begin,worker_exit_code=None,parameters=case.parameters,reused=False)
            write_json(target/'execution.json',row);row['attempt_manifest_sha256']=seal(target);finished[case.id]=row;note('finished',case,status='worker_start_error');return False
        active[case.id]=dict(case=case,path=target,process=process,stdout=stdout,stderr=stderr,started=begin)
        write_json(target/'launch.json',dict(process_id=process.pid,process_group=process.pid,started_monotonic=begin))
        note('started',case,memory_reservation_mb=case.memory_limit_mb);return True

    try:
        while cursor<len(plan.cases) or active:
            now=time.monotonic();forced=None
            if cancel_file is not None and Path(cancel_file).exists():forced='cancelled'
            elif now-started>=plan.maximum_wall_s:forced='campaign_timeout'
            elif output_exceeded(out):forced='output_budget_exceeded'
            if forced:
                halt=forced
                for key,job in list(active.items()):finish(job,forced);del active[key]
            else:
                # Reap all observed exits before making additional admissions.
                for key,job in list(active.items()):
                    reason='worker_timeout' if now-job['started']>=job['case'].worker_wall_s else None
                    if reason or job['process'].poll() is not None:
                        finish(job,reason);del active[key]
                        if plan.stop_on_failure and not finished[key]['passed']:halt='stop_on_failure'
            if halt:
                for case in plan.cases[cursor:]:finished[case.id]=dict(id=case.id,status='not_run_'+halt,passed=False,parameters=case.parameters)
                cursor=len(plan.cases)
            else:
                while cursor<len(plan.cases):
                    case=plan.cases[cursor]
                    if cancel_file is not None and Path(cancel_file).exists():halt='cancelled';break
                    if time.monotonic()-started>=plan.maximum_wall_s:halt='campaign_timeout';break
                    if output_exceeded(out):halt='output_budget_exceeded';break
                    if case.id in prior:
                        target=out/'attempts'/case.id;shutil.copytree(parent/'attempts'/case.id,target)
                        if verify(target)!=prior[case.id]['attempt_manifest_sha256']:raise ValueError('copied attempt differs')
                        finished[case.id]=dict(prior[case.id],reused=True);note('reused',case);cursor+=1
                        if plan.stop_on_failure and not finished[case.id]['passed']:halt='stop_on_failure';break
                        continue
                    reserved=sum(j['case'].memory_limit_mb for j in active.values())
                    if len(active)>=plan.maximum_workers or reserved+case.memory_limit_mb>plan.memory_budget_mb:break
                    launched=launch(case);cursor+=1
                    peak_workers=max(peak_workers,len(active));peak_memory=max(peak_memory,sum(j['case'].memory_limit_mb for j in active.values()))
                    if not launched and plan.stop_on_failure:halt='stop_on_failure';break
            if active:time.sleep(.025)
    except BaseException:
        for job in active.values():
            try:stop_worker(job['process'])
            finally:job['stdout'].close();job['stderr'].close()
        raise
    if code!=package_fingerprint():raise ValueError('campaign producer changed')
    if resume_from is not None and verify(parent)!=parent_manifest:raise ValueError('continuation parent changed during execution')
    rows=[finished[c.id] for c in plan.cases]
    result=dict(schema='power-grid-env.campaign-result/v1',id=plan.id,completed=all(r.get('assessment',{}).get('numerically_completed',False) for r in rows),passed=all(r['passed'] for r in rows),cases=rows,plan_sha256=digest(encoded(payload)),code_sha256=code,elapsed_s=time.monotonic()-started,resumed_from_manifest_sha256=parent_manifest,recovered_attempts={k:v['attempt_manifest_sha256'] for k,v in prior.items()} if recover_from is not None else {},halt_reason=halt,scheduler=dict(policy='fifo_local_processes_v1',maximum_workers=plan.maximum_workers,memory_budget_mb=plan.memory_budget_mb,peak_active_workers=peak_workers,peak_reserved_memory_mb=peak_memory))
    write_json(out/'scheduler.json',audit);write_json(out/'summary.json',result);_report(out,plan,rows);seal(out);verify_campaign(out)
    return result


def verify_scheduler(root,plan,result):
    """Recompute reservation peaks from the persisted admission/reaping log."""
    metadata=result.get('scheduler',{});events=read_json(root/'scheduler.json')
    if not isinstance(metadata,dict) or not isinstance(events,list) or any(not isinstance(e,dict) for e in events):raise ValueError('invalid scheduler document')
    if (metadata.get('policy')!='fifo_local_processes_v1'
            or metadata.get('maximum_workers')!=plan.maximum_workers
            or metadata.get('memory_budget_mb')!=plan.memory_budget_mb):raise ValueError('scheduler policy differs')
    if any(type(metadata.get(k)) is not int for k in ('maximum_workers','memory_budget_mb','peak_active_workers','peak_reserved_memory_mb')):raise ValueError('invalid scheduler resource counts')
    by_id={c.id:c for c in plan.cases};rows={r['id']:r for r in result['cases']}
    order={c.id:i for i,c in enumerate(plan.cases)};active=set();seen=set();last_index=-1;last_time=0.;peak_workers=0;peak_memory=0
    import math
    for event in events:
        key=event.get('case_id');kind=event.get('kind');at=event.get('elapsed_s')
        if key not in by_id or type(at) not in (int,float) or not math.isfinite(at) or at<last_time:raise ValueError('invalid scheduler event')
        last_time=at
        if kind in {'started','reused'} or (kind=='finished' and key not in active):
            if key in seen or order[key]<=last_index:raise ValueError('scheduler admission order differs')
            seen.add(key);last_index=order[key]
        if kind=='started':
            if event.get('memory_reservation_mb')!=by_id[key].memory_limit_mb or rows[key].get('reused'):raise ValueError('scheduler reservation differs')
            active.add(key)
        elif kind=='finished':
            if event.get('status')!=rows[key]['status']:raise ValueError('scheduler completion differs')
            if key in active:active.remove(key)
            elif rows[key]['status']!='worker_start_error':raise ValueError('scheduler finished an unstarted case')
        elif kind=='reused':
            if rows[key].get('reused') is not True:raise ValueError('scheduler reuse differs')
        else:raise ValueError('unknown scheduler event')
        reserved=sum(by_id[k].memory_limit_mb for k in active)
        if len(active)>plan.maximum_workers or reserved>plan.memory_budget_mb:raise ValueError('scheduler resource budget exceeded')
        peak_workers=max(peak_workers,len(active));peak_memory=max(peak_memory,reserved)
    expected={r['id'] for r in result['cases'] if r.get('attempt_manifest_sha256')}
    if active or seen!=expected:raise ValueError('scheduler has unaccounted processes')
    if metadata.get('peak_active_workers')!=peak_workers or metadata.get('peak_reserved_memory_mb')!=peak_memory:raise ValueError('scheduler resource peak differs')

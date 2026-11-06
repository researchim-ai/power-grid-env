"""Versioned local engineering studies with bounded workers and immutable runs."""
from contextlib import contextmanager
from pathlib import Path
import json,math,os,re,shutil,signal,subprocess,sys,time
from power_grid_env.study_artifacts import read_json,encoded,digest,inventory,seal,verify,export_zip,extract_zip,MAX_FILE,MAX_TOTAL
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash,validate_network_rules
from power_grid_env.evidence import package_fingerprint
from power_grid_env.paths import package_dir
from power_grid_env.report import write_json

SCHEMA='power-grid-env.engineering-project/v1'
KINDS={'power_flow':'opendss','qsts':'opendss','rms':'andes','small_signal':'andes','motor_start':'scipy','coupled_rms':'scipy','coupled_small_signal':'scipy','flux_network':'scipy'}


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):raise ValueError('ID must be 1..64 ASCII letters/digits/_/-')
    return value


def completeness(network,report=None):
    report=report or {};rows=report.get('commands',[]);unsupported=[r for r in rows if r.get('status')=='unsupported']
    omissions=[x.model_dump(mode='json') for x in network.omissions]
    return dict(command_counts={k:sum(r.get('status')==k for r in rows) for k in ['transferred','unsupported','ignored_non_electrical','directive']},
        unsupported_commands=unsupported,omissions=omissions,approximations=report.get('approximations',[]),assumptions=report.get('assumptions',[]),
        equipment_counts={k:len(getattr(network,k)) for k in ['sources','lines','transformers','loads','generators','inverters','storages','switches','shunts']},
        fully_transferred=not unsupported and not omissions and not report.get('approximations') and not report.get('errors'),
        source_command_ledger_available=bool(rows),errors=report.get('errors',[]))


def check_spec(spec):
    errors=validate_network_rules(spec.network);warnings=list(spec.network.assumptions);scope={}
    if spec.schema_version!='power-grid-env.study/v1' or KINDS.get(spec.study)!=spec.backend:errors.append('unsupported study/backend/schema')
    expected={'motor_start':'induction','coupled_rms':'coupled','coupled_small_signal':'coupled','rms':'rms','small_signal':'rms','flux_network':'flux_network','qsts':'scenario'}.get(spec.study)
    if spec.requested_motors:errors.append('requested_motors is not an executable model passport')
    from power_grid_env.results import SolveOptions
    if spec.study!='power_flow' and spec.options!=SolveOptions():errors.append('this study requires engine-managed default SolveOptions')
    for key in ['induction','coupled','rms','flux_network','scenario']:
        if key==expected and getattr(spec,key) is None:errors.append('required settings missing: '+key)
        if key!=expected and getattr(spec,key) is not None:errors.append('unused settings must be removed: '+key)
    if not errors:
        try:
            if spec.study=='motor_start':
                from power_grid_env.induction_network import validate_induction
                scope=validate_induction(spec.network,spec.induction)
            elif spec.study in {'coupled_rms','coupled_small_signal'}:
                from power_grid_env.coupled_rms import validate_coupled
                scope=validate_coupled(spec.network,spec.coupled)
                if spec.study=='coupled_small_signal' and spec.coupled.network_automation is not None:errors.append('automation small-signal profile unsupported')
            elif spec.study in {'rms','small_signal'}:
                from power_grid_env.rms_study import validate_rms
                scope=validate_rms(spec.network,spec.rms)
            elif spec.study=='flux_network':
                from power_grid_env.flux_network import FluxNetwork
                from power_grid_env.core_flux_network import CoreFluxNetwork
                cls=CoreFluxNetwork if any(p.core_loss_s for p in spec.flux_network.motors) else FluxNetwork
                model=cls(spec.network,spec.flux_network);scope=dict(ok=True,states=model.dimension,profile='balanced full-winding RL')
            else:
                from power_grid_env.preflight import preflight
                scope=preflight(spec.network,task='power_flow',solve_initial=False)
            if spec.study=='qsts':
                names={load.id for load in spec.network.loads}
                for name,points in spec.scenario.load_profile.items():
                    if name not in names or not points or not points[0] or points[0][0]!=0:
                        errors.append('invalid_load_profile: '+name);continue
                    previous=-1.
                    for row in points:
                        if len(row) not in {2,3} or any(not math.isfinite(v) for v in row) or row[0]<=previous:
                            errors.append('invalid_load_profile: '+name);break
                        previous=row[0]
            errors.extend(scope.get('errors',[]));warnings.extend(scope.get('warnings',[]))
        except (ValueError,RuntimeError,ArithmeticError) as exc:errors.append(str(exc))
    return dict(ok=not errors,errors=errors,warnings=warnings,scope=scope,study_sha256=digest(encoded(spec.model_dump(mode='json'))),network_sha256=canonical_hash(spec.network),
        units=dict(network_base='VA',voltage='line-line RMS V',power='three-phase W/var unless per-phase dictionary',time='s',flux_profile='instantaneous peak SI alpha-beta',per_unit='explicit own-equipment bases'),
        equipment=completeness(spec.network),code_sha256=package_fingerprint())


class EngineeringProject:
    def __init__(self,root):
        self.root=Path(root)
        if self.root.is_symlink():raise ValueError('project symlink forbidden')
        self.root=self.root.resolve()

    @contextmanager
    def lock(self):
        fd=os.open(self.root/'.project.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            os.write(fd,str(os.getpid()).encode());yield
        finally:os.close(fd);(self.root/'.project.lock').unlink(missing_ok=True)

    @classmethod
    def create(cls,root,spec,*,title=None,import_report=None):
        root=Path(root)
        if root.exists():raise ValueError('new project directory required')
        payload=spec.model_dump(mode='json');encoded(payload)
        root.mkdir(parents=True);project=cls(root);(root/'revisions').mkdir();(root/'runs').mkdir()
        write_json(root/'project.json',dict(schema=SCHEMA,id=spec.id,title=title or spec.id,revisions=[],runs=[]))
        project.revise(spec,import_report=import_report);return project

    def descriptor(self):
        for p in self.root.rglob('*'):
            if p.is_symlink():raise ValueError('project symlink forbidden')
        data=read_json(self.root/'project.json')
        if data.get('schema')!=SCHEMA:raise ValueError('unsupported project schema')
        for row in data['revisions']:
            identifier(row['id']);path=self.root/'revisions'/(row['id']+'.json')
            if digest(path.read_bytes())!=row['sha256']:raise ValueError('study revision changed')
            imported=self.root/'revisions'/(row['id']+'-import.json')
            if imported.exists()!=bool(row.get('import_sha256')) or (imported.exists() and digest(imported.read_bytes())!=row['import_sha256']):raise ValueError('import provenance changed')
        if len({r['id'] for r in data['runs']})!=len(data['runs']):raise ValueError('duplicate run IDs')
        for row in data['runs']:identifier(row['id'])
        return data

    def revise(self,spec,*,import_report=None):
        payload=spec.model_dump(mode='json');raw=encoded(payload)
        if len(raw)>MAX_FILE:raise ValueError('study byte budget exceeded')
        with self.lock():
            data=self.descriptor();id_='r'+str(len(data['revisions'])+1).zfill(4);path=self.root/'revisions'/(id_+'.json')
            with path.open('xb') as f:f.write(raw)
            if import_report is not None:write_json(self.root/'revisions'/(id_+'-import.json'),import_report)
            row=dict(id=id_,sha256=digest(raw))
            if import_report is not None:row['import_sha256']=digest((self.root/'revisions'/(id_+'-import.json')).read_bytes())
            data['revisions'].append(row);write_json(self.root/'project.json',data)
        return id_

    def study(self,revision=None):
        data=self.descriptor()
        if not data['revisions']:raise ValueError('project has no revision')
        id_=revision or data['revisions'][-1]['id'];identifier(id_)
        if id_ not in {r['id'] for r in data['revisions']}:raise ValueError('unknown revision')
        return id_,StudySpec.model_validate(read_json(self.root/'revisions'/(id_+'.json')))

    def check(self,revision=None):
        id_,spec=self.study(revision);result=check_spec(spec);p=self.root/'revisions'/(id_+'-import.json')
        if p.exists():result['equipment']=completeness(spec.network,read_json(p))
        return result

    def request_cancel(self):(self.root/'cancel').write_text('1')
    def clear_cancel(self):(self.root/'cancel').unlink(missing_ok=True)

    def verify_run(self,id_):
        identifier(id_);data=self.descriptor();row=next((r for r in data['runs'] if r['id']==id_),None)
        if row is None:raise ValueError('unknown run')
        path=self.root/'runs'/id_
        if verify(path)!=row['manifest_sha256']:raise ValueError('run manifest changed')
        return path,read_json(path/'run.json')

    def run(self,id_,*,revision=None,resume_from=None,max_steps=None,timeout_s=120.,checkpoint_every=96):
        identifier(id_)
        if not math.isfinite(timeout_s) or not 0<timeout_s<=3600:raise ValueError('bounded positive timeout required')
        if type(checkpoint_every) is not int or checkpoint_every<1 or (max_steps is not None and (type(max_steps) is not int or max_steps<1)):raise ValueError('positive integer step/checkpoint budgets required')
        with self.lock():
            data=self.descriptor()
            if id_ in {r['id'] for r in data['runs']} or (self.root/'runs'/id_).exists():raise ValueError('run IDs are immutable; choose a new one')
            rev,spec=self.study(revision);checked=self.check(rev)
            if resume_from and spec.study!='qsts':raise ValueError('resume is QSTS-only')
            if max_steps is not None and spec.study!='qsts':raise ValueError('step budget is QSTS-only')
            parent=None
            if resume_from:
                parent,meta=self.verify_run(resume_from)
                if meta['status'] not in {'paused','cancelled','timeout','runtime_limit_exceeded'}:raise ValueError('parent run is not resumable')
                if not (parent/'engine/checkpoint.json').is_file():raise ValueError('parent has no committed checkpoint')
                if digest(encoded(read_json(parent/'study.json')))!=checked['study_sha256']:raise ValueError('resume study differs')
            path=self.root/'runs'/id_;path.mkdir();write_json(path/'study.json',spec.model_dump(mode='json'));write_json(path/'preflight.json',checked)
            p=self.root/'revisions'/(rev+'-import.json')
            if p.exists():shutil.copyfile(p,path/'import.json')
            if parent is not None:
                shutil.copytree(parent/'engine',path/'engine');(path/'engine/.lock').unlink(missing_ok=True)
            options=dict(resume=bool(resume_from),max_steps=max_steps,checkpoint_every=checkpoint_every,cancel_file=str(self.root/'cancel'))
            write_json(path/'worker-options.json',options)
            status='rejected';code=package_fingerprint();exitcode=None
            if checked['ok']:
                if (self.root/'cancel').exists():status='cancelled'
                else:
                    env=os.environ.copy();env['PYTHONPATH']=str(package_dir().parent)
                    with (path/'stdout.log').open('wb') as stdout,(path/'stderr.log').open('wb') as stderr:
                        process=subprocess.Popen([sys.executable,'-m','power_grid_env.project_worker',str(path/'study.json'),str(path),str(path/'worker-options.json')],cwd=path,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
                        started=time.monotonic();cancel_at=None;reason=None
                        while process.poll() is None:
                            sizes=[p.stat().st_size for p in path.rglob('*') if p.is_file()]
                            if time.monotonic()-started>timeout_s:reason='timeout'
                            elif max(sizes,default=0)>MAX_FILE or sum(sizes)>MAX_TOTAL:reason='runtime_limit_exceeded'
                            elif (self.root/'cancel').exists():
                                cancel_at=cancel_at or time.monotonic()
                                if spec.study!='qsts' or time.monotonic()-cancel_at>2:reason='cancelled'
                            if reason:
                                try:os.killpg(process.pid,signal.SIGKILL)
                                except ProcessLookupError:pass
                                process.wait();break
                            time.sleep(.025)
                        exitcode=process.wait();status=reason or 'failed'
            if (path/'result.json').exists():
                result=read_json(path/'result.json');identity=result.get('workbench_execution',{})
                if identity.get('code_sha256')!=code or identity.get('study_sha256')!=checked['study_sha256']:raise ValueError('worker result identity mismatch')
                if exitcode==0 and status not in {'timeout','runtime_limit_exceeded','cancelled'}:status=result.get('status','completed' if result.get('completed') else 'failed')
            else:result=dict(completed=False,status=status)
            if code!=package_fingerprint():raise ValueError('producer changed during study')
            # A killed oversized worker must not create an unexportable artifact.
            sizes=[p.stat().st_size for p in path.rglob('*') if p.is_file()]
            if max(sizes,default=0)>MAX_FILE or sum(sizes)>MAX_TOTAL:
                raise ValueError('worker exceeded hard artifact limit; run left unsealed for inspection')
            meta=dict(id=id_,revision=rev,parent_run=resume_from,status=status,completed=bool(result.get('completed',False)) and status not in {'timeout','runtime_limit_exceeded','cancelled','rejected','failed'},code_sha256=code,
                study_sha256=checked['study_sha256'],worker_exitcode=exitcode,scope='local engineering run; numerical completion is not equipment acceptance')
            write_json(path/'run.json',meta)
            from power_grid_env.engineering_report import write_engineering_report
            try:write_engineering_report(path,spec,result,meta)
            except (ValueError,ArithmeticError) as exc:
                meta.update(status='engineering_report_failed',completed=False,report_error=str(exc));write_json(path/'run.json',meta)
                write_json(path/'report-error.json',dict(error=str(exc)))
                status=meta['status']
            manifest=seal(path);data['runs'].append(dict(id=id_,revision=rev,manifest_sha256=manifest,status=status));write_json(self.root/'project.json',data)
            return meta

    def export(self,id_,target):
        path,_=self.verify_run(id_);return export_zip(path,target)

    @classmethod
    def from_export(cls,archive,root):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='pge-study-import-') as tmp:
            path=extract_zip(archive,Path(tmp)/'data');spec=StudySpec.model_validate(read_json(path/'study.json'))
            report=read_json(path/'import.json') if (path/'import.json').exists() else None
            project=cls.create(root,spec,import_report=report)
            shutil.copytree(path,project.root/'runs/imported');meta=read_json(path/'run.json');data=project.descriptor()
            data['runs'].append(dict(id='imported',revision='r0001',manifest_sha256=verify(path),status=meta['status']))
            write_json(project.root/'project.json',data);return project


def import_engineering_project(source,root,*,sidecar=None):
    """Import with explicit transfer coverage; failed imports never reuse a model."""
    from power_grid_env.importer import import_network
    network,report=import_network(Path(source),Path(sidecar) if sidecar else None)
    if network is None:
        target=Path(root)
        if target.exists():raise ValueError('new import directory required')
        target.mkdir(parents=True);write_json(target/'import-report.json',report)
        return dict(ok=False,errors=report.get('errors',[]),report=str(target/'import-report.json'))
    spec=StudySpec(id=network.id,study='power_flow',backend='opendss',network=network)
    project=EngineeringProject.create(root,spec,import_report=report)
    return project.check()

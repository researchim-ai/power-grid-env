"""Collect independently sealed cases and prove the sample-reporting-only change."""
import argparse
import ast
import copy
import hashlib
from pathlib import Path
import shutil
import zipfile
import numpy as np
from benchmarks.continuous_drive_acceptance import NAMES,FRACTIONS,REFERENCES,REFERENCE_UNCERTAINTY_FRACTION,producer,document
from benchmarks.continuous_drive_reference import WHEEL_SHA256,VERSION
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal
from power_grid_env.switching_drive import SwitchingDriveStudy
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from benchmarks.switching_adaptive_budget_qualification import mesh_audit

PREVIOUS_WHEEL_SHA256='3712b8d879f5013dc4466ee4a1f37e5d450fb1f6fc92a6ec32337a6f0bf7470c'
PREVIOUS_PACKAGE='0da006ecde9bb4172dd80e422e09102bfaee128b8fad04c59288ec05caf1aa24'


def reporting_only_ast(old,current):
    before=ast.parse(old);after=ast.parse(current)
    run=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='run_switching_drive')
    record=next(n for n in run.body if isinstance(n,ast.FunctionDef) and n.name=='record')
    expected=ast.parse('scheduled=round(t/p.sample_s)*p.sample_s\nreported=p.duration_s if abs(t-p.duration_s)<clock_tolerance else scheduled if abs(t-scheduled)<clock_tolerance else t').body
    if [ast.dump(n) for n in record.body[:2]]!=[ast.dump(n) for n in expected]:return False
    if sum(isinstance(n,ast.Name) and n.id=='reported' and isinstance(n.ctx,ast.Load) for stmt in record.body[2:] for n in ast.walk(stmt))!=2:return False
    class Rename(ast.NodeTransformer):
        def visit_Name(self,node):
            if node.id=='reported':node.id='t'
            return node
    record.body=[Rename().visit(n) for n in record.body[2:]]
    return ast.dump(before)==ast.dump(after)


def source_compatibility(wheel):
    raw=Path(wheel).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PREVIOUS_WHEEL_SHA256:raise ValueError('previous reporting wheel hash differs')
    import power_grid_env.switching_drive as runtime
    root=Path(runtime.__file__).parent;current={p.name:p.read_bytes() for p in root.glob('*.py')}
    with zipfile.ZipFile(wheel) as z:old={Path(n).name:z.read(n) for n in z.namelist() if n.startswith('power_grid_env/') and n.count('/')==1 and n.endswith('.py')}
    if set(old)!=set(current):raise ValueError('runtime source inventory changed')
    delta=[n for n in old if old[n]!=current[n]]
    if delta!=['switching_drive.py'] or not reporting_only_ast(old['switching_drive.py'],current['switching_drive.py']):raise ValueError('change exceeds declared sample reporting fix')
    digest=hashlib.sha256()
    for name in sorted(old):digest.update(name.encode());digest.update(old[name])
    if digest.hexdigest()!=PREVIOUS_PACKAGE:raise ValueError('previous runtime fingerprint differs')
    return dict(previous_package=PREVIOUS_PACKAGE,current_package=package_fingerprint(),changed_sources=delta,reporting_only_ast=True,previous_wheel_sha256=PREVIOUS_WHEEL_SHA256)


def output_delta(study,old,new):
    a=copy.deepcopy(old);b=copy.deepcopy(new);ta=np.asarray(a.pop('time_s'));tb=np.asarray(b.pop('time_s'))
    if ta.shape!=tb.shape or np.max(abs(ta-tb))>5e-13:raise ValueError('clock correction exceeds registered tolerance')
    ga=a['channels'].pop('gates_enabled');gb=b['channels'].pop('gates_enabled')
    if a!=b:raise ValueError('physical waveform, ledger, control or solver counters changed')
    changed=[]
    for i,(left,right) in enumerate(zip(ga,gb)):
        if left==right:continue
        old_blocked=any(x<=ta[i]<y for x,y in study.gate_block_windows);new_blocked=any(x<=tb[i]<y for x,y in study.gate_block_windows)
        boundaries=[t for pair in study.gate_block_windows for t in pair]
        if old_blocked==new_blocked or right!=float(not new_blocked) or min((abs(tb[i]-t) for t in boundaries),default=1.)>5e-13:raise ValueError('gate permission changed beyond clock-boundary correction')
        changed.append(dict(index=i,old_time_s=float(ta[i]),new_time_s=float(tb[i]),old_value=left,new_value=right))
    return dict(physical_data_bitwise_equal=True,maximum_clock_correction_s=float(max(abs(ta-tb))),gate_permission_changes=changed)


def collect(paths,before_paths,wheel,previous_wheel,out):
    compatibility=source_compatibility(previous_wheel);identity=producer();limits=SwitchingTemporalLimits()
    if hashlib.sha256(Path(wheel).read_bytes()).hexdigest()!=WHEEL_SHA256:raise ValueError('external machine wheel differs')
    with zipfile.ZipFile(wheel) as z:native={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('motulator/') and n.endswith('.py')}
    def inputs(paths,current):
        mapping={}
        for raw in paths:
            path=Path(raw);digest=verify(path);p=read_json(path/'protocol.json')
            if len(p['cases'])!=1 or p['cases'][0] in mapping:raise ValueError('one unique registered case per input required')
            name=p['cases'][0]
            if name not in NAMES or p['references']!=list(REFERENCES) or p['limits']!=limits.model_dump(mode='json'):raise ValueError('case/reference controls/limits differ')
            if current and p['producer']!=identity:raise ValueError('current run producer differs')
            if not current and p['producer']['package']!=PREVIOUS_PACKAGE:raise ValueError('before-run producer differs')
            mapping[name]=(path,digest)
        if set(mapping)!=set(NAMES):raise ValueError('complete four-case inventory required')
        return mapping
    after=inputs(paths,True);before=inputs(before_paths,False);rows=[];deltas=[]
    for name in NAMES:
        path,_=after[name];old_path,_=before[name];p=document(name);study=SwitchingDriveStudy.model_validate(p)
        if read_json(path/name/'study.json')!=p or read_json(old_path/name/'study.json')!=p:raise ValueError('current/before/reference study differs')
        refs=[read_json(path/name/f'reference-{i}.json') for i in range(2)]
        for r,settings in zip(refs,REFERENCES):
            if r['native_source_sha256']!=native or not r['reference']['production_imports_blocked'] or r['reference']['machine_version']!=VERSION:raise ValueError('native identity/isolation differs')
            for key,value in settings.items():
                if r['reference']['max_step_s' if key=='max_step' else key]!=value:raise ValueError('continuous solver controls differ')
        agreement=differences(study,*refs,limits);audits=[audited(study,r,limits) for r in refs]
        reference_passed=agreement['passed'] and all(v['maximum_normalized']<=REFERENCE_UNCERTAINTY_FRACTION for v in agreement['groups'].values()) and all(all(a.values()) for a in audits) and all(r['metrics']['maximum_energy_relative_error']<1e-8 for r in refs)
        checks=[]
        for index,fraction in enumerate(FRACTIONS):
            expected=copy.deepcopy(p);expected['adaptive']={'error_budget_fraction':fraction};s=SwitchingDriveStudy.model_validate(expected)
            if read_json(path/name/f'primary-{index}-study.json')!=s.model_dump(mode='json') or read_json(old_path/name/f'primary-{index}-study.json')!=s.model_dump(mode='json'):raise ValueError('primary input changed')
            current=read_json(path/name/f'primary-{index}.json');previous=read_json(old_path/name/f'primary-{index}.json');mesh_audit(s,current)
            delta=output_delta(study,previous,current);deltas.append(dict(case=name,fraction=fraction,**delta))
            comparisons=[differences(study,current,r,limits) for r in refs];a=audited(study,current,limits);checks.append(dict(fraction=fraction,passed=all(a.values()) and all(v['passed'] for v in comparisons),audits=a,comparisons=comparisons))
        rows.append(dict(case=name,passed=reference_passed and all(c['passed'] for c in checks),reference_passed=reference_passed,reference_agreement=agreement,reference_audits=audits,primary_checks=checks))
    if producer()!=identity:raise RuntimeError('collection producer changed')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    for name,(path,_) in after.items():shutil.copytree(path/name,out/name)
    result=dict(schema='power-grid-env.continuous-drive-collection/v1',scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),passed=all(r['passed'] for r in rows),rows=rows,output_deltas=deltas,source_compatibility=compatibility,producer=identity,current_manifests={k:v[1] for k,v in after.items()},before_manifests={k:v[1] for k,v in before.items()},primary_trajectories=8,continuous_reference_trajectories=8,physical_waveforms_unchanged=True,external_machine=True,whole_external_simulator=False,absolute_error_certified=False,scope='four authored positive-Gc single-cage full-drive profiles; original motulator motor RHS and independently assembled circuit/control; two continuous methods and fixed physical caps; declared output-clock correction only; no universal ranking/OEM accuracy')
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='append',required=True);p.add_argument('--before',action='append',required=True);p.add_argument('--wheel',required=True);p.add_argument('--previous-wheel',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=collect(a.run,a.before,a.wheel,a.previous_wheel,a.out);print(r['passed']);raise SystemExit(0 if r['passed'] else 2)

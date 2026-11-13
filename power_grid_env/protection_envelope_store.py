"""Composite finite-corner studies without weakening per-artifact byte limits.

Only index/ is a generic sealed artifact. Each studies/<fixed-name>/ is sealed
separately and pinned by that index. The store root is not a generic artifact.
"""
from pathlib import Path
import time
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans,row,summary
from power_grid_env.closed_loop_protection import run_closed_loop,verify_closed_loop
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from power_grid_env.evidence import package_fingerprint

INDEX_FILES={'plan.json','entries.json','result.json','manifest.json'}


def layout(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('store root must be a real directory')
    if {p.name for p in root.iterdir()}!={'index','studies'}:raise ValueError('unindexed store content')
    for key in ['index','studies']:
        if (root/key).is_symlink() or not (root/key).is_dir():raise ValueError('store directories must not be links')
    if {p.name for p in (root/'index').iterdir()}!=INDEX_FILES:raise ValueError('index file coverage differs')
    return root


def entry(name,recipe,result,study):
    identity=verify(study)
    manifest=read_json(Path(study)/'manifest.json')
    return dict(id=name,recipe=recipe,study_manifest_sha256=identity,
        result_sha256=digest(encoded(result)),row=row(name,result),
        completed_grid_runs=sum(len(r['grids']) for r in result['actions']),
        payload_bytes=sum(v['bytes'] for v in manifest['files'].values()))


def store_result(envelope,entries,code):
    result=summary(envelope,[e['row'] for e in entries],code)
    result['schema']='power-grid-env.protection-envelope-store-result/v1'
    result.update(expected_grid_runs=17*3*(len(envelope.base.equipment.mandatory_cases)+3),
        completed_grid_runs=sum(e['completed_grid_runs'] for e in entries),
        payload_bytes=sum(e['payload_bytes'] for e in entries),
        storage='individually bounded studies and a sealed manifest index; no aggregate artifact budget override')
    return result


def run_store(envelope,out,*,cancelled=None):
    e=ProtectionEnvelope.model_validate(envelope);variants=list(effective_plans(e))
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    index=root/'index';data=root/'studies';index.mkdir();data.mkdir()
    (index/'plan.json').write_bytes(encoded(e.model_dump(mode='json')))
    code=package_fingerprint();started=time.monotonic();entries=[]
    stop=lambda:(cancelled is not None and cancelled()) or time.monotonic()-started>=e.total_timeout_s
    for name,plan,recipe in variants:
        if stop():break
        result=run_closed_loop(plan,data/name,cancelled=stop)
        # Complete negative profiles remain first-class evidence. Incomplete
        # workers can be indexed but can never pass complete-store verification.
        if result['completed']:verify_closed_loop(data/name,expected_plan=plan)
        entries.append(entry(name,recipe,result,data/name))
    if package_fingerprint()!=code:raise ValueError('producer changed during store execution')
    result=store_result(e,entries,code)
    (index/'entries.json').write_bytes(encoded(entries));(index/'result.json').write_bytes(encoded(result));seal(index)
    return result


def verify_store(root,*,expected_envelope=None):
    root=layout(root);index=root/'index';data=root/'studies';before=verify(index)
    e=ProtectionEnvelope.model_validate(read_json(index/'plan.json'))
    if expected_envelope is not None and ProtectionEnvelope.model_validate(expected_envelope)!=e:raise ValueError('owner envelope mismatch')
    variants=list(effective_plans(e));names=[v[0] for v in variants]
    if {p.name for p in data.iterdir()}!=set(names):raise ValueError('complete17-study coverage required; extra studies forbidden')
    stored=read_json(index/'entries.json')
    if not isinstance(stored,list) or len(stored)!=len(names) or any(not isinstance(v,dict) for v in stored) or [v.get('id') for v in stored]!=names:raise ValueError('corner index order/coverage differs')
    entries=[]
    for stored_entry,(name,plan,recipe) in zip(stored,variants,strict=True):
        if stored_entry.get('recipe')!=recipe:raise ValueError('corner recipe differs')
        study=data/name
        if stored_entry.get('study_manifest_sha256')!=verify(study):raise ValueError('indexed study manifest differs')
        verify_closed_loop(study,expected_plan=plan)
        result=read_json(study/'result.json')
        expected=entry(name,recipe,result,study)
        if stored_entry!=expected:raise ValueError('indexed study metadata differs')
        entries.append(expected)
    result=store_result(e,entries,package_fingerprint())
    if not result['completed'] or read_json(index/'result.json')!=result:raise ValueError('store summary or completed coverage differs')
    if verify(index)!=before:raise ValueError('store index changed during verification')
    return dict(verified=True,index_manifest_sha256=before,owner_envelope_anchored=expected_envelope is not None,**result)

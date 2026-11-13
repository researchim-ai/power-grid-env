"""Reproducible bounded catalog matrix; no remote lookup or GitHub service."""
import argparse
import copy
from pathlib import Path
from power_grid_env.equipment_catalog import run_catalog,verify_catalog,compile_catalog
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint


def variants(catalog,brief):
    # Authored fixture protocol: named 250/400 kVA families, not arbitrary catalogs.
    p=dict(cable_id='nexans-084193001',transformer_id='trihal36-250')
    result=[]
    for name in ['trihal250-air30','trihal400-air30','trihal250-air40','breaker-rejected']:
        c=copy.deepcopy(catalog);b=copy.deepcopy(brief);s=copy.deepcopy(p)
        if name=='trihal400-air30':s['transformer_id']='trihal36-400'
        if name=='trihal250-air40':
            c['assumptions']['temperature_factor']=copy.deepcopy(c['air_temperature_factors'][1]['factor'])
            b['installation'].update(ambient_c=40,temperature_factor=.91)
        if name=='breaker-rejected':b['installation']['breaker_interrupting_a']=100
        result.append((name,c,b,s))
    return result


def summary(rows):
    return dict(passed=True,code_sha256=package_fingerprint(),physical_grid_runs=36,variants=rows,
        manufacturer_extracts=True,manufacturer_authenticated=False,unverified_owner_assumptions=True,
        normative_approval=False,document_bytes_archived=False)


def row(name,result):
    r=result
    if not r['equipment']['completed'] or not r['equipment']['numerically_qualified']:
        raise ValueError('incomplete or unqualified catalog variant: '+name)
    if r['equipment']['meets_declared_requirements']!=(name!='breaker-rejected'):
        raise ValueError('unexpected acceptance decision: '+name)
    return dict(id=name,status=r['status'],reward=r['reward'],static_pass=r['equipment']['static_checks_passed'],dynamic_pass=r['equipment']['dynamic_checks_passed'],owner_anchored=True)


def run_matrix(catalog,brief,out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[]
    for name,c,b,s in variants(catalog,brief):
        r=run_catalog(c,b,s,root/name)
        verify_catalog(root/name,expected_catalog=c,expected_brief=b,expected_selection=s)
        rows.append(row(name,r));print(rows[-1],flush=True)
    result=summary(rows);(root/'summary.json').write_bytes(encoded(result));seal(root);return result


def verify_matrix(catalog,brief,root):
    root=Path(root);before=verify(root);rows=[]
    for name,c,b,s in variants(catalog,brief):
        r=verify_catalog(root/name,expected_catalog=c,expected_brief=b,expected_selection=s)
        rows.append(row(name,r))
    if read_json(root/'summary.json')!=summary(rows):raise ValueError('catalog matrix summary differs')
    if verify(root)!=before:raise ValueError('matrix changed during verification')
    return dict(verified=True,manifest_sha256=before,**summary(rows))


def reference_matrix(catalog,brief,out):
    from benchmarks.equipment_sizing_reference import run
    root=Path(out);root.mkdir(parents=True,exist_ok=False);results=[]
    for name,c,b,s in variants(catalog,brief)[:2]:
        task=compile_catalog(c,b,s)[3]
        results.append(run(task.sizing,s,root/name))
    result=dict(passed=all(r['passed'] for r in results),code_sha256=package_fingerprint(),
        independent_fault_solutions=4,public_checks_per_selection=4,
        dynamic_external_validation=False,manufacturer_authenticated=False)
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['generate','verify','reference']);p.add_argument('--catalog',required=True);p.add_argument('--input',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    f=dict(generate=run_matrix,verify=verify_matrix,reference=reference_matrix)[a.action]
    print(encoded(f(read_json(a.catalog),read_json(a.input),a.out)).decode())

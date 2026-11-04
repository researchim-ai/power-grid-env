"""Recompute guard audits and comparisons from pinned, sealed native evidence.

No FMU is compiled or stepped. Current production equations must match the
historical producer; all consumed inputs are verified before and after use.
"""
from pathlib import Path
import json,hashlib,math
from benchmarks.generator_esst1a_refinement import case,NAMES
from benchmarks.generator_esst1a_joint_validation import compare
from benchmarks.mnlex2_guard_audit import audit
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    def nonfinite(value):raise ValueError('nonfinite JSON input: '+value)
    return json.loads(Path(path).read_text(),parse_constant=nonfinite)


def recheck(delivery,corpus,out,delivery_sha256):
    from benchmarks.native_online_validation import source_identity
    delivery=Path(delivery).resolve();corpus=Path(corpus).resolve();out=Path(out).resolve()
    if len(delivery_sha256)!=64 or any(c not in '0123456789abcdef' for c in delivery_sha256):raise ValueError('explicit SHA256 pin required')
    if sha(delivery)!=delivery_sha256:raise ValueError('historical delivery hash differs')
    if not corpus.is_relative_to(ROOT):raise ValueError('historical corpus must be inside this workspace')
    if any(out==p or p in out.parents for p in (delivery.parent,corpus)):raise ValueError('output must be outside historical evidence')
    seal=load(delivery)
    if seal.get('sealed') is not True or seal.get('passed') is not True:raise ValueError('sealed historical delivery required')
    code=package_fingerprint();producer=source_identity()
    if seal.get('production_code_sha256')!=code:raise ValueError('production changed; new native simulations required')
    index=delivery.parent/'external-evidence-sha256.json'
    index_sha=sha(index)
    if seal.get('artifacts_sha256',{}).get(index.name)!=index_sha:raise ValueError('historical evidence index differs')
    manifest=load(index);consumed={}
    def read(path):
        resolved=path.resolve()
        if not resolved.is_relative_to(ROOT):raise ValueError('historical input escapes workspace')
        key=path.relative_to(ROOT).as_posix();actual=sha(path)
        if manifest.get(key)!=actual:raise ValueError('historical input hash differs: '+key)
        consumed[key]=actual;return load(path)
    protocol=read(corpus/'protocol.json');historical=read(corpus/'summary.json')
    names=tuple(protocol['cases']);tolerances=protocol['native_tolerances']
    if not names or not set(names)<=set(NAMES):raise ValueError('registered causal case passports required')
    if protocol['code_sha256']!=code or protocol.get('native_UEL_event_policy')!='causal':raise ValueError('matching causal producer required')
    if len(tolerances)<2 or len(set(tolerances))!=len(tolerances) or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('two unique finite native tolerances required')
    if any(case(n).model_dump(mode='json')!=protocol['cases'][n] for n in names):raise ValueError('registered case passport changed')
    pairs=[(r['case'],r['tolerance']) for r in historical['runs']]
    if historical.get('status')!='complete' or len(set(pairs))!=len(pairs) or set(pairs)!={(n,t) for n in names for t in tolerances}:raise ValueError('complete historical case/tolerance matrix required')
    out.mkdir(parents=True,exist_ok=False)
    write_json(out/'summary.json',dict(passed=False,audit_completed=False,native_simulations_rerun=False))
    rows=[];resets=low=0
    for name in names:
        s=case(name);a=read(corpus/name/'primary.json');directory=out/name;directory.mkdir()
        for tolerance in tolerances:
            b=read(corpus/name/str(tolerance)/'reference.json');guard=audit(s,b);check=compare(s,a,b)
            target=directory/str(tolerance);target.mkdir()
            write_json(target/'guard-audit.json',guard);write_json(target/'comparison.json',check)
            resets+=guard['resets'];low+=guard['low_local_separation_resets']
            rows.append(dict(case=name,tolerance=tolerance,passed=check['passed'],guard_audit_passed=guard['passed'],resets=guard['resets'],low_local_separation_resets=guard['low_local_separation_resets']))
    if sha(delivery)!=delivery_sha256 or sha(index)!=index_sha or any(sha(ROOT/p)!=h for p,h in consumed.items()):raise ValueError('historical evidence changed during audit')
    if code!=package_fingerprint() or producer!=source_identity():raise ValueError('audit producer changed')
    result=dict(passed=all(r['passed'] for r in rows),audit_completed=True,native_simulations_rerun=False,industrial_profile_qualified=False,
        native_UEL_root_policy=protocol.get('native_UEL_root_policy','compound_and_atoms'),code_sha256=code,source_sha256=producer,historical_delivery_sha256=delivery_sha256,consumed_inputs_sha256=consumed,
        audited_resets=resets,low_local_separation_resets=low,conditioning_is_diagnostic_only=True,runs=rows)
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--delivery',required=True,type=Path);p.add_argument('--delivery-sha256',required=True);p.add_argument('--corpus',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--require-agreement',action='store_true')
    a=p.parse_args();r=recheck(a.delivery,a.corpus,a.out,a.delivery_sha256)
    print(json.dumps(dict(audit_completed=r['audit_completed'],all_comparisons_agree=r['passed'],audited_resets=r['audited_resets'],native_simulations_rerun=False)))
    raise SystemExit(1 if a.require_agreement and not r['passed'] else 0)

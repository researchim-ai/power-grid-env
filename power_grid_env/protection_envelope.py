"""Finite Cartesian pickup/time corners, not a continuous robustness proof."""
import itertools
import time
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract
from power_grid_env.closed_loop_protection import ClosedLoopPlan,compile_closed_loop,run_closed_loop,verify_closed_loop
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from power_grid_env.evidence import package_fingerprint

DIMENSIONS=['primary.pickup_a','primary.time_multiplier','backup.pickup_a','backup.time_multiplier']


class ProtectionEnvelope(Contract):
    schema_version: Literal['power-grid-env.protection-envelope/v1']='power-grid-env.protection-envelope/v1'
    id: str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')
    base: ClosedLoopPlan
    total_timeout_s: float=Field(default=3600,gt=0,le=7200)
    @model_validator(mode='after')
    def profile(self):
        if self.base.clock_policy!='sampled_integral':raise ValueError('envelope requires independent sampled clock audit')
        if not any(getattr(getattr(self.base,s),k)>0 for s in ['primary','backup'] for k in ['pickup_tolerance','time_tolerance']):
            raise ValueError('nonzero tolerance required for corner study')
        compile_closed_loop(self.base)
        return self


def effective_plans(envelope):
    e=ProtectionEnvelope.model_validate(envelope)
    entries=[('nominal',None)]+[('corner-'+''.join('0' if x<0 else '1' for x in signs),signs) for signs in itertools.product([-1,1],repeat=4)]
    for name,signs in entries:
        p=e.base.model_dump(mode='json');p['id']=e.id+'-'+name
        if signs is not None:
            for (stage,key),sign in zip([('primary','pickup_a'),('primary','time_multiplier'),('backup','pickup_a'),('backup','time_multiplier')],signs):
                tol='pickup_tolerance' if key=='pickup_a' else 'time_tolerance'
                p[stage][key]*=1+sign*p[stage][tol]
            # Tolerances have been applied to concrete parameters, not retained
            # as an unexecuted second uncertainty layer.
            for stage in ['primary','backup']:
                p[stage]['pickup_tolerance']=0.;p[stage]['time_tolerance']=0.
        p=ClosedLoopPlan.model_validate(p);compile_closed_loop(p)
        yield name,p,dict(dimensions=DIMENSIONS,signs=list(signs) if signs is not None else None,
                          primary=p.primary.model_dump(mode='json'),backup=p.backup.model_dump(mode='json'))


def row(name,result):
    return dict(id=name,completed=result['completed'],numerically_qualified=result['numerically_qualified'],
                passes_declared_profile=result['passes_declared_profile'])


def summary(e,rows,code):
    complete=len(rows)==17 and all(r['completed'] for r in rows)
    qualified=complete and all(r['numerically_qualified'] for r in rows)
    return dict(schema='power-grid-env.protection-envelope-result/v1',plan_sha256=digest(encoded(e.model_dump(mode='json'))),
        code_sha256=code,completed=complete,numerically_qualified=bool(qualified),rows=rows,
        passes_all_enumerated_corners=bool(qualified and all(r['passes_declared_profile'] for r in rows)),
        expected_variants=17,expected_corners=16,reward=None,normative_approval=False,
        continuous_uncertainty_qualified=False,
        scope='nominal plus all16 pickup/time sign corners of two relays; fixed opening delays, reset and dropout model; no interior/cold-hot/topology/OEM uncertainty proof')


def run_envelope(envelope,out,*,cancelled=None):
    e=ProtectionEnvelope.model_validate(envelope);variants=list(effective_plans(e))
    root=Path(out);root.mkdir(parents=True,exist_ok=False);code=package_fingerprint();started=time.monotonic();rows=[]
    (root/'plan.json').write_bytes(encoded(e.model_dump(mode='json')))
    stop=lambda:(cancelled is not None and cancelled()) or time.monotonic()-started>=e.total_timeout_s
    for name,p,recipe in variants:
        if stop():break
        result=run_closed_loop(p,root/name,cancelled=stop)
        (root/(name+'-recipe.json')).write_bytes(encoded(recipe));rows.append(row(name,result))
    if package_fingerprint()!=code:raise ValueError('producer changed during corner study')
    result=summary(e,rows,code);(root/'result.json').write_bytes(encoded(result));seal(root);return result


def verify_envelope(root,*,expected_envelope=None):
    root=Path(root);before=verify(root);e=ProtectionEnvelope.model_validate(read_json(root/'plan.json'))
    if expected_envelope is not None and ProtectionEnvelope.model_validate(expected_envelope)!=e:raise ValueError('owner envelope mismatch')
    rows=[]
    for name,p,recipe in effective_plans(e):
        if read_json(root/(name+'-recipe.json'))!=recipe:raise ValueError('corner derivation mismatch')
        result=verify_closed_loop(root/name,expected_plan=p);rows.append(row(name,result))
    result=summary(e,rows,package_fingerprint())
    if not result['completed'] or read_json(root/'result.json')!=result:raise ValueError('corner coverage or verdict differs')
    if verify(root)!=before:raise ValueError('corner evidence changed')
    return dict(verified=True,manifest_sha256=before,owner_envelope_anchored=expected_envelope is not None,**result)

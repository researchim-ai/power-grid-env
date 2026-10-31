"""Probe native limiter rollback against a fresh small-step reference."""
from pathlib import Path
import argparse,json
import numpy as np
from benchmarks.automatic_motor_group import example
from benchmarks.andes_group_motor_reference import worker
from benchmarks.native_checkpoint import NativeCheckpoint
from power_grid_env.report import write_json,sha256_file


class ProbeFinished(Exception):pass


def probe(name,out):
    spec=example(name);settings=spec.coupled if spec.coupled else spec.induction
    for m in settings.motors:m.contactor=None;m.thermal=None
    result={}
    def driver(ctx):
        a=ctx['system']
        if spec.coupled:
            a.SEXS.vref0.v[0]+=10.;ctx['project']()
        checkpoint=NativeCheckpoint(a);before=checkpoint.expected_digest
        def advance(h):
            a.TDS.h=h;a.dae.t[...]=h
            ok=bool(a.TDS.itm_step());return ok,a.dae.x.copy(),a.dae.y.copy(),a.dae.f.copy()
        ok,x,y,f=advance(.0001)
        if not ok:raise ValueError('fresh small step failed')
        checkpoint.restore();trial_ok,_,_,_=advance(.01)
        mutated=checkpoint.digest()!=before
        upper_flags={name+'.'+key:getattr(b,'zu',np.array([])).tolist() for name,m in a.exist.pflow_tds.items() for key,b in m.discrete.items() if type(b).__name__=='AntiWindup'}
        checkpoint.restore();restored=checkpoint.digest()==before and checkpoint.arrays_keep_identity()
        replay_ok,rx,ry,rf=advance(.0001)
        error=max(float(np.max(abs(v-w))) if len(v) else 0. for v,w in [(x,rx),(y,ry),(f,rf)])
        result.update(case=name,passed=bool(mutated and restored and replay_ok and error<=1e-10),discarded_trial_converged=trial_ok,state_mutated_by_trial=mutated,complete_restore_verified=restored,maximum_replay_error=error,limiter_upper_flags_after_discarded_trial=upper_flags,discrete_inventory=checkpoint.discrete_inventory,checkpoint_items=len(checkpoint.items),package_unchanged=True,native_closed_loop_controller_qualified=False,field_validated=False)
        raise ProbeFinished()
    try:worker(spec,.00025,driver=driver)
    except ProbeFinished:pass
    write_json(Path(out),result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--out',required=True,type=Path);a=p.parse_args();r=probe(a.case,a.out);print(json.dumps(r))
    if not r.get('passed'):raise SystemExit(1)

"""Local before/after port microbenchmark; no whole-simulator speed ranking.

Baseline retains the pre-cache radial-gradient and affine assembly methods.
Both paths use identical passport/inputs, BLAS/thread environment and outputs.
"""
import argparse
import hashlib
from pathlib import Path
import platform
import os
import time
import numpy as np
from benchmarks.switching_thermal_cases import document
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,seal


class PreCachePort(SwitchingMagnetics):
    def radial_gradient(self,old,mid):
        p=self.p;new=2*mid-old;a=float(np.linalg.norm(old));b=float(np.linalg.norm(new));power=p.saturation_exponent+2
        coefficient=.5/p.unsaturated_h;gradient_coefficient=np.zeros(2)
        if p.saturation_beta_per_vs and a+b>0:
            # (b**power-a**power)/(b*b-a*a), with its equal-radius limit.
            total=sum(b**(power-1-k)*a**k for k in range(power))
            derivative=sum((power-1-k)*b**(power-2-k)*a**k for k in range(power-1))
            scale=p.saturation_beta_per_vs**p.saturation_exponent/(power*p.unsaturated_h)
            coefficient+=scale*total/(a+b)
            if b>0:gradient_coefficient=scale*(derivative*(a+b)-total)/(a+b)**2*(2*new/b)
        value=2*mid*coefficient
        jac=2*coefficient*np.eye(2)+2*np.outer(mid,gradient_coefficient)
        return value,jac

    def affine(self,old,h,speed,guess,temperature=None):
        p=self.p;rs,rr=self.resistances(temperature);matrix=self.linear_reluctance.copy();gradient,jac=self.radial_gradient(old[:2],guess[:2]);matrix[:2,:2]+=jac-np.eye(2)/p.unsaturated_h
        offset=np.zeros(self.size);offset[:2]=gradient-jac@guess[:2]
        # Current-coordinate gradient rows, plus explicit core feedthrough
        # expressed in flux increments. This yields a Norton voltage port.
        physical=matrix.copy();physical[:2,:2]+=p.core_loss_s*2/h*np.eye(2)
        physical_offset=offset.copy();physical_offset[:2]-=p.core_loss_s*2/h*old[:2]
        resistance=np.repeat(np.r_[rs,rr],2)
        left=2/h*np.eye(self.size)+resistance[:,None]*physical
        spin=np.array([[0.,-1.],[1.,0.]])*p.pole_pairs*speed
        for k in range(1,self.n):left[2*k:2*k+2,2*k:2*k+2]-=spin
        rhs=-resistance*(matrix@old+offset)
        for k in range(1,self.n):rhs[2*k:2*k+2]+=spin@old[2*k:2*k+2]
        B=np.vstack([np.eye(2),np.zeros((self.size-2,2))])
        solved=np.linalg.solve(left,np.column_stack([rhs,B]));offset_delta=solved[:,0];offset_flux=old+offset_delta;gain_flux=solved[:,1:]
        motor_offset=(matrix@old+offset)[:2]+physical[:2]@offset_delta;motor_gain=physical[:2]@gain_flux
        return dict(offset_delta=offset_delta,offset_flux=offset_flux,gain_flux=gain_flux,motor_offset=motor_offset,motor_gain=motor_gain,
                    physical_matrix=physical,physical_offset=physical_offset,gradient=gradient,jacobian=jac,guess=guess.copy())


def run(out,repetitions=7,calls=1500):
    before=package_fingerprint();rows=[]
    for name in ['warm_winding','saturated_four_thermal']:
        passport=FluxMachinePassport.model_validate(document(name)['motor'])
        ports={'before':PreCachePort(passport),'cached':SwitchingMagnetics(passport)}
        x=np.tile([.4,.1],ports['cached'].n);guess=x+.0001
        args=(x,1e-5,35.,guess,[90.,70.])
        baseline=ports['before'].affine(*args);current=ports['cached'].affine(*args)
        for key in ['offset_delta','gain_flux','motor_offset','motor_gain']:
            np.testing.assert_allclose(current[key],baseline[key],rtol=1e-12,atol=1e-13)
        measurements={k:[] for k in ports}
        for q in ports.values():
            for _ in range(100):q.affine(*args)
        for repeat in range(repetitions):
            order=['before','cached'] if repeat%2==0 else ['cached','before']
            for label in order:
                start=time.perf_counter()
                for _ in range(calls):ports[label].affine(*args)
                measurements[label].append(time.perf_counter()-start)
        rows.append(dict(case=name,cages=len(passport.cages),passport=passport.model_dump(mode='json'),inputs=dict(old_flux=x.tolist(),guess_flux=guess.tolist(),step_s=1e-5,speed_rad_s=35.,temperature_c=[90.,70.]),measurements_s=measurements,median_speedup=float(np.median(measurements['before'])/np.median(measurements['cached']))))
    if package_fingerprint()!=before:raise ValueError('producer changed during timing')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    result=dict(schema='power-grid-env.switching-port-timing/v1',passed=True,package_sha256=before,benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),repetitions=repetitions,calls_per_repetition=calls,rows=rows,environment=dict(python=platform.python_version(),numpy=np.__version__,platform=platform.platform(),OPENBLAS_NUM_THREADS=os.environ.get('OPENBLAS_NUM_THREADS'),OMP_NUM_THREADS=os.environ.get('OMP_NUM_THREADS')),scope='two fixed operating points; alternating warm microbenchmark; equality checked; no required speedup, no full-trajectory or external-simulator superiority established')
    (out/'timing.json').write_bytes(encoded(result));seal(out);print(encoded(result).decode())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args();run(args.out)

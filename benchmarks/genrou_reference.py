"""Independent six-state GENROU ODE with algebraic network and quadratic saturation.

Specific to the lossless external SMIB fixture, with arbitrary machine Ra.
No ANDES/project machine equations are called by the reference integration.
"""
from pathlib import Path
import argparse
import cmath
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import root
from power_grid_env.rms_study import positive_sequence,RoundRotorMachine
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json


def saturation_curve(machine, flux):
    if machine.saturation=='disabled':return 0.
    a=np.sqrt(machine.saturation_s10/(1.2*machine.saturation_s12))
    threshold=(1-1.2*a)/(1-a)
    gain=machine.saturation_s10/(1-threshold)**2
    return gain*max(flux-threshold,0.)**2/flux if flux>0 else 0.


def independent_reference(spec,times,*,linearize=False):
    n=spec.network;r=spec.rms;m=r.machines[0]
    assert isinstance(m,RoundRotorMachine) and len(r.machines)==len(n.generators)==len(n.sources)==len(n.lines)==1
    assert not (m.governor or m.exciter or m.stabilizer or n.loads or n.transformers)
    assert all(e.kind=='mechanical_step' for e in r.events)
    g=n.generators[0];src=n.sources[0];line=n.lines[0]
    z=positive_sequence(np.asarray(line.r_ohm)+1j*np.asarray(line.x_ohm))
    assert abs(z.real)<1e-10 and src.r_ohm==0 and src.angle_a_rad==0 and not np.any(line.b_siemens)
    zb=g.ll_v**2/m.rated_va;xe=(src.x_ohm+z.imag)/zb;vs=src.ll_v/g.ll_v
    p=g.p_w/m.rated_va;theta=np.arcsin(p*xe/(g.v_set_pu*vs))
    v=g.v_set_pu*cmath.exp(1j*theta);current=(v-vs)/(1j*xe)
    ra=m.armature_r_pu;xd=m.x_d_pu;xq=m.x_q_pu;xd1=m.x_d_prime_pu;xq1=m.x_q_prime_pu
    xd2=m.x_d_subtransient_pu;xq2=m.x_q_subtransient_pu;xl=m.leakage_pu
    gd1=(xd2-xl)/(xd1-xl);gd2=(xd1-xd2)/(xd1-xl)**2
    gq1=(xq2-xl)/(xq1-xl);gq2=(xq1-xq2)/(xq1-xl)**2;gqd=(xq-xl)/(xd-xl)
    def initial_dq(delta):
        ir=current*cmath.exp(-1j*delta);vr=v*cmath.exp(-1j*delta)
        iq=ir.real;id_=-ir.imag;vq=vr.real;vd=-vr.imag
        pd=vq+xd2*id_+ra*iq;pq=vd-xq2*iq+ra*id_
        return id_,iq,pd,pq
    def q_balance(x):
        id_,iq,pd,pq=initial_dq(x[0]);se=saturation_curve(m,np.hypot(pd,pq))
        return [(1+se*gqd)*pq-(xq-xq2)*iq]
    delta_guess=cmath.phase(v+(ra+1j*xq)*current)
    solved=root(q_balance,[delta_guess],tol=1e-11);assert solved.success or abs(q_balance(solved.x)[0])<1e-10
    delta=float(solved.x[0]);id_,iq,pd,pq=initial_dq(delta)
    se=saturation_curve(m,np.hypot(pd,pq));vf=pd+(xd-xd2)*id_+se*pd
    state=np.array([delta,1.,pd+(xd1-xd2)*id_,pq-(xq1-xq2)*iq,pd+(xl-xd2)*id_,pq+(xq2-xl)*iq])
    pm0=p+ra*abs(current)**2
    times=np.asarray(times);reference=np.empty((len(times),6))
    boundaries=sorted({0.,r.duration_s,*(e.time_s for e in r.events),*(f.apply_s for f in r.faults),*(f.clear_s for f in r.faults)})
    def rhs_for(pm,yf):
        def rhs(t,x):
            delta,w,e1q,e1d,e2d,e2q=x
            pd=gd1*e1q+gd2*(xd1-xl)*e2d;pq=gq1*e1d+(1-gq1)*e2q
            emf=complex(pd,-pq)*cmath.exp(1j*delta);yg=1/(ra+1j*xd2);ys=1/(1j*xe)
            terminal=(emf*yg+vs*ys)/(yg+ys+yf)
            ig=(emf-terminal)*yg;ir=ig*cmath.exp(-1j*delta);id_=-ir.imag;iq=ir.real
            se=saturation_curve(m,np.hypot(pd,pq))
            airgap=(terminal*np.conj(ig)).real+ra*abs(ig)**2
            ad=e1q+(xd-xd1)*(gd1*id_-gd2*e2d+gd2*e1q)+se*pd
            aq=e1d+(xq-xq1)*(gq2*e1d-gq2*e2q-gq1*iq)+se*pq*gqd
            return [2*np.pi*n.frequency_hz*(w-1),(pm-airgap-m.damping_pu*(w-1))/(2*m.inertia_s),
                    (vf-ad)/m.td_prime_s,-aq/m.tq_prime_s,
                    (-e2d+e1q-(xd1-xl)*id_)/m.td_subtransient_s,
                    (-e2q+e1d+(xq1-xl)*iq)/m.tq_subtransient_s]
        return rhs
    assert max(abs(np.array(rhs_for(pm0,0.)(0,state))))<1e-8
    if linearize:
        derivative=rhs_for(pm0,0.)
        step=1e-6
        columns=[]
        for i in range(6):
            perturb=np.zeros(6);perturb[i]=step
            columns.append((np.array(derivative(0,state+perturb))-np.array(derivative(0,state-perturb)))/(2*step))
        return np.column_stack(columns)

    for left,right in zip(boundaries,boundaries[1:]):
        mid=(left+right)/2
        pm=pm0+sum(e.value for e in r.events if e.time_s<mid)
        yf=sum(zb/complex(f.r_ohm,f.x_ohm) for f in r.faults if f.apply_s<mid<f.clear_s)
        sol=solve_ivp(rhs_for(pm,yf),(left,right),state,rtol=1e-10,atol=1e-12,dense_output=True)
        assert sol.success
        selected=(times>=left)&(times<=right);reference[selected]=sol.sol(times[selected]).T;state=sol.y[:,-1]
    return reference


def run(source,out):
    spec=StudySpec.model_validate_json(source.read_text());rows=[]
    for h in [.001,.0005]:
        s=spec.model_copy(deep=True);s.rms.step_s=h
        result=run_study(s);assert result['completed'],result
        ref=independent_reference(s,result['time_s']);g=s.network.generators[0].id
        names=['delta','omega','e1q','e1d','e2d','e2q']
        errors={name:float(max(abs(ref[:,i]-result['channels'][g+'.'+name]))) for i,name in enumerate(names)}
        passed=errors['delta']<1e-4 and errors['omega']<1e-5 and max(errors[k] for k in names[2:])<1e-3
        rows.append({'step_s':h,'errors':errors,'verdict':'pass' if passed else 'fail','code_sha256':result['code_sha256']})
        write_json(out/f'trajectory-{h}.json',result)
    payload={'verdict':'pass' if all(r['verdict']=='pass' for r in rows) else 'fail',
             'reference':'independent six-state GENROU and network RK45','cases':rows,
             'saturation':spec.rms.machines[0].saturation,'industrial_profile_qualified':False,'field_validated':False}
    write_json(out/'summary.json',payload);return payload


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('spec',type=Path);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();r=run(args.spec,args.out);print(r);raise SystemExit(0 if r['verdict']=='pass' else 1)

"""Independent reduced-order network + governor/exciter/PSS ODE reference.

This oracle is specific to the declared lossless one-machine fixture. It does
not call ANDES or project controller blocks to integrate its reference states.
"""
from pathlib import Path
import argparse
import cmath
import numpy as np
from scipy import signal
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.rms_study import positive_sequence
from power_grid_env.topology import compile_topology
from power_grid_env.report import write_json


def independent_reference(spec,times):
    net=spec.network;settings=spec.rms
    assert len(net.generators)==len(net.sources)==len(net.lines)==len(settings.machines)==1
    assert not net.loads and not net.transformers
    machine=settings.machines[0];g=machine.governor;a=machine.exciter;p=machine.stabilizer
    assert g and a and p and machine.model=='GENCLS'
    assert all(e.kind in {'governor_step','voltage_reference_step'} for e in settings.events)
    gen=net.generators[0];source=net.sources[0];line=net.lines[0]
    z=positive_sequence(np.asarray(line.r_ohm)+1j*np.asarray(line.x_ohm))
    assert source.r_ohm==0 and abs(z.real)<1e-10 and not np.any(line.b_siemens)
    assert source.angle_a_rad==0
    base_z=gen.ll_v**2/machine.rated_va
    xe=(z.imag+source.x_ohm)/base_z;xd=machine.x_d_prime_pu;vs=source.ll_v/gen.ll_v
    pm=gen.p_w/machine.rated_va
    theta=np.arcsin(pm*xe/(gen.v_set_pu*vs))
    v=gen.v_set_pu*cmath.exp(1j*theta);e=v+1j*xd*(v-vs)/(1j*xe)
    vref0=gen.v_set_pu+abs(e)/a.K
    # PSS transfer function assembled independently from the published blocks.
    numerator=np.polymul([p.A6,p.A5,1],[p.T1,1]);numerator=np.polymul(numerator,[p.T3,1]);numerator=np.polymul(numerator,[p.KS*p.T5,0])
    denominator=np.polymul([p.A2,p.A1,1],[p.A4,p.A3,1]);denominator=np.polymul(denominator,[p.T2,1]);denominator=np.polymul(denominator,[p.T4,1]);denominator=np.polymul(denominator,[p.T6,1])
    A,B,C,D=signal.tf2ss(numerator,denominator)
    state=np.r_[cmath.phase(e),1.,pm,pm,abs(e)/a.K,abs(e),np.zeros(len(A))]
    events=sorted({0.,settings.duration_s,*(e.time_s for e in settings.events),*(f.apply_s for f in settings.faults),*(f.clear_s for f in settings.faults)})
    ref=np.empty((len(times),len(state)))
    def outputs(x,y_fault):
        angle,w,valve,lag,avr_lag,field=x[:6];pss_state=x[6:]
        emf=field*cmath.exp(1j*angle)
        voltage=(emf/(1j*xd)+vs/(1j*xe))/(1/(1j*xd)+1/(1j*xe)+y_fault)
        pe=(emf*np.conj((emf-voltage)/(1j*xd))).real
        pss_raw=float((C@pss_state).item()+D.item()*(w-1))
        stabilizing=float(np.clip(pss_raw,p.LSMIN,p.LSMAX)) if p.VCL<abs(voltage)<p.VCU else 0.
        pout=g.T2/g.T3*valve+(1-g.T2/g.T3)*lag-g.Dt*(w-1)
        return voltage,pe,stabilizing,pout
    for left,right in zip(events,events[1:]):
        midpoint=(left+right)/2
        pref=pm+sum(ev.value for ev in settings.events if ev.kind=='governor_step' and ev.time_s<midpoint)
        vref=vref0+sum(ev.value for ev in settings.events if ev.kind=='voltage_reference_step' and ev.time_s<midpoint)
        y_fault=sum(base_z/complex(f.r_ohm,f.x_ohm) for f in settings.faults if f.apply_s<midpoint<f.clear_s)
        def rhs(t,x):
            voltage,pe,pss,pout=outputs(x,y_fault)
            angle,w,valve,lag,avr_lag,field=x[:6]
            valve_rate=(pref-(w-1)/g.R-valve)/g.T1
            if (valve>=g.VMAX and valve_rate>0) or (valve<=g.VMIN and valve_rate<0):valve_rate=0.
            vi=vref-abs(voltage)+pss
            field_rate=(a.K*(a.TATB*vi+(1-a.TATB)*avr_lag)-field)/a.TE
            if (field>=a.EMAX and field_rate>0) or (field<=a.EMIN and field_rate<0):field_rate=0.
            return np.r_[2*np.pi*net.frequency_hz*(w-1),(pout-pe-machine.damping_pu*(w-1))/(2*machine.inertia_s),
                         valve_rate,(valve-lag)/g.T3,(vi-avr_lag)/a.TB,field_rate,A@x[6:]+B.ravel()*(w-1)]
        solution=solve_ivp(rhs,(left,right),state,rtol=1e-10,atol=1e-12,dense_output=True)
        assert solution.success
        selected=(times>=left)&(times<=right);ref[selected]=solution.sol(times[selected]).T
        state=solution.y[:,-1]
    return ref


def run(source,out):
    spec=StudySpec.model_validate_json(source.read_text());rows=[]
    for step in (.002,.001):
        s=spec.model_copy(deep=True);s.rms.step_s=step
        r=run_study(s);assert r['completed'],r
        ref=independent_reference(s,np.asarray(r['time_s']));c=r['channels'];gen=s.network.generators[0].id
        errors={'delta_rad':float(max(abs(ref[:,0]-c[gen+'.delta']))),
                'omega_pu':float(max(abs(ref[:,1]-c[gen+'.omega']))),
                'field_pu':float(max(abs(ref[:,5]-c[gen+'.SEXS.vout']))),
                'valve_pu':float(max(abs(ref[:,2]-c[gen+'.TGOV1.LAG_y'])))}
        passed=errors['delta_rad']<1e-4 and errors['omega_pu']<1e-5 and errors['field_pu']<1e-3 and errors['valve_pu']<1e-4
        rows.append({'step_s':step,'errors':errors,'verdict':'pass' if passed else 'fail','code_sha256':r['code_sha256']})
        write_json(out/f'trajectory-{step}.json',r)
    result={'verdict':'pass' if all(row['verdict']=='pass' for row in rows) else 'fail',
            'reference':'independent coupled network/swing/TGOV1/SEXS/IEEEST SciPy RK45',
            'cases':rows,'field_validated':False,'industrial_profile_qualified':False,
            'scope':'declared lossless reduced-order one-machine controller and fault fixture'}
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('spec',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=run(a.spec,a.out);print(r);raise SystemExit(0 if r['verdict']=='pass' else 1)

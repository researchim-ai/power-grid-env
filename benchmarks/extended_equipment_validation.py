"""Bounded full-winding references: original motulator and exact linear RL solution.

Native flux coordinates are integrated before the primary current-coordinate
model. Thermal constants and extra cages are authored stress data, not OEM data.
"""
from pathlib import Path
import argparse, hashlib, json, math, zipfile
import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import expm
from power_grid_env.build import NetBuilder
from power_grid_env.flux_machine import FluxMachinePassport, WindingThermal
from power_grid_env.flux_network import FluxNetworkSettings, run_flux_network
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json, sha256_file
from benchmarks.motulator_motor_validation import WHEEL_SHA256

PUBLISHED='https://aalto-electric-drives.github.io/motulator/drive_examples/current_vector/plot_2kw_im_sat_cvc.html'
NAMES=('saturated_dol','linear_dol','thermal_sag','saturated_locked','exact_one_cage','exact_two_cages','exact_four_cages')
LIMITS=dict(current_a=2e-5,flux_vs=2e-7,speed_rad_s=2e-5,torque_nm=2e-5,temperature_c=2e-6)


def example(name):
    if name not in NAMES: raise ValueError('unknown equipment case')
    exact=name.startswith('exact_'); cages={'exact_two_cages':2,'exact_four_cages':4}.get(name,1)
    b=NetBuilder('extended-'+name,'Published Gamma machine; authored network and thermal stress',.4,s_base_va=5000)
    b.node('grid'); b.source('grid',.05,0.,ll_v=400.)
    node='grid'
    if exact:
        b.node('motor'); b.line_z1('grid','motor',.12+.23j,line_id='feeder');node='motor'
    p=FluxMachinePassport(id='m',node_id=node,stator_r_ohm=3.7,unsaturated_h=.34,
        saturation_beta_per_vs=0. if exact or name=='linear_dol' else .84,
        cages=[dict(resistance_ohm=2.5+j,leakage_h=.023+.011*j) for j in range(cages)],
        pole_pairs=2,inertia_kg_m2=.03,fan_nm_per_rad_s2=14.6/(math.pi*50)**2,
        locked_rotor=exact or name=='saturated_locked',
        provenance=dict(electrical=PUBLISHED,magnetics=PUBLISHED,mechanical='J=0.03 kg m2; authored mechanical benchmark',
                        extra_cages='Authored passive RL branches, not measured skin-depth data'))
    events=[]
    if name=='thermal_sag':
        p.thermal=WindingThermal(stator_capacity_j_per_k=10,rotor_capacity_j_per_k=8,
            stator_ambient_w_per_k=1,rotor_ambient_w_per_k=1,cross_w_per_k=.2)
        p.provenance['thermal']='Authored accelerated two-node thermal stress, not OEM heat capacities'
        events=[dict(time_s=.025,source_id='source',voltage_pu=.5),dict(time_s=.04,source_id='source',voltage_pu=1.)]
    return b.build(),FluxNetworkSettings(motors=[p],duration_s=.06,step_s=.0005,events=events)


def axis(settings):
    t=np.r_[np.arange(math.ceil(settings.duration_s/settings.step_s))*settings.step_s,settings.duration_s]
    return sorted(set(t.tolist()+[e.time_s for e in settings.events]))


def verify_native(wheel):
    import motulator
    from importlib.metadata import version
    if version('motulator')!='0.19.0' or sha256_file(wheel)!=WHEEL_SHA256:raise ValueError('pinned native wheel required')
    root=Path(motulator.__file__).parent; hashes={}
    with zipfile.ZipFile(wheel) as archive:
        for source in sorted(root.rglob('*.py')):
            name=source.relative_to(root).as_posix();data=source.read_bytes()
            if data!=archive.read('motulator/'+name):raise ValueError('native source differs from wheel: '+name)
            hashes[name]=hashlib.sha256(data).hexdigest()
    return hashes


def native_reference(network,settings):
    """One-bus resistive source, original library electromagnetic/mechanical RHS."""
    from motulator.drive.model import InductionMachine,InductionMachinePars,MechanicalSystem
    p=settings.motors[0]
    if network.lines or len(p.cages)!=1:raise ValueError('native reference is explicitly single-bus/single-cage')
    source=network.sources[0];cage=p.cages[0]
    machine=InductionMachine(InductionMachinePars(n_p=p.pole_pairs,R_s=p.stator_r_ohm,R_r=cage.resistance_ohm,L_ell=cage.leakage_h,
        L_s=lambda rho:p.unsaturated_h/(1+(p.saturation_beta_per_vs*rho)**p.saturation_exponent)))
    mechanics=MechanicalSystem(p.inertia_kg_m2,B_L=lambda w:p.fan_nm_per_rad_s2*abs(w)+p.viscous_nm_per_rad_s)
    scale=1.;thermal=p.thermal
    def outputs(t,x):
        machine.state.psi_s_ab=complex(*x[:2]);machine.state.psi_r_ab=complex(*x[2:4]);machine.inp.w_M=x[4]
        if thermal:
            machine.par.R_s=p.stator_r_ohm*(1+thermal.alpha_per_c*(x[5]-thermal.reference_c))
            machine.par.R_r=cage.resistance_ohm*(1+thermal.alpha_per_c*(x[6]-thermal.reference_c))
        current,rotor,_=machine.compute_outputs(machine.state,machine.inp)
        machine.inp.u_s_ab=source.ll_v*math.sqrt(2/3)*scale*np.exp(1j*2*math.pi*network.frequency_hz*t)-source.r_ohm*current
        machine.set_outputs(t);mechanics.state.w_M=complex(x[4]);mechanics.inp.tau_M=machine.out.tau_M
        return current,rotor
    def rhs(t,x):
        current,rotor=outputs(t,x);ds,dr=machine.rhs(t);_,dw=mechanics.rhs(t)
        row=[ds.real,ds.imag,dr.real,dr.imag,0. if p.locked_rotor else float(np.real(dw))]
        if thermal:
            loss_s=1.5*machine.par.R_s*abs(current)**2;loss_r=1.5*machine.par.R_r*abs(rotor)**2
            exchange=thermal.cross_w_per_k*(x[5]-x[6])
            row += [(loss_s-thermal.stator_ambient_w_per_k*(x[5]-thermal.ambient_c)-exchange)/thermal.stator_capacity_j_per_k,
                    (loss_r-thermal.rotor_ambient_w_per_k*(x[6]-thermal.ambient_c)+exchange)/thermal.rotor_capacity_j_per_k]
        return row
    x=np.r_[np.zeros(4),p.initial_speed_rad_s,*([thermal.ambient_c]*2 if thermal else [])]
    times=axis(settings); states={0.:x.copy()};events={e.time_s:e.voltage_pu for e in settings.events}
    boundaries=[0.,*sorted(events),settings.duration_s]
    for left,right in zip(boundaries,boundaries[1:]):
        grid=[t for t in times if left<t<=right]
        solution=solve_ivp(rhs,(left,right),x,method='DOP853',rtol=2e-11,atol=1e-12,max_step=.0001,t_eval=grid)
        if not solution.success:raise ValueError('native integration failed')
        states.update(zip(solution.t,solution.y.T.copy()));x=solution.y[:,-1].copy()
        if right in events:scale=events[right]
    rows=[]
    for t in times:
        x=states[t];scale=next((e.voltage_pu for e in reversed(settings.events) if e.time_s<=t),1.)
        current,rotor=outputs(t,x)
        row=dict(current_alpha_a=current.real,current_beta_a=current.imag,cage1_current_alpha_a=rotor.real,cage1_current_beta_a=rotor.imag,
                 stator_flux_alpha_vs=x[0],stator_flux_beta_vs=x[1],cage1_flux_alpha_vs=x[2],cage1_flux_beta_vs=x[3],speed_rad_s=x[4],torque_nm=float(machine.out.tau_M))
        if thermal:row.update(stator_temperature_c=x[5],rotor_temperature_c=x[6])
        rows.append(row)
    return dict(completed=True,time_s=times,channels={'m.'+k:[float(r[k]) for r in rows] for k in rows[0]},
                original_motulator_electrical_and_mechanical_rhs=True,thermal_balance_independently_authored=bool(thermal))


def exact_reference(network,settings):
    """Closed-form augmented complex matrix exponential; no production kernel."""
    p=settings.motors[0];n=len(p.cages)+1;w=2*math.pi*network.frequency_hz
    if p.saturation_beta_per_vs or not p.locked_rotor or p.thermal or settings.events:raise ValueError('exact linear locked-rotor profile only')
    line=network.lines[0];external_l=(line.x_ohm[0][0]-line.x_ohm[0][1])/w
    external_r=network.sources[0].r_ohm+line.r_ohm[0][0]-line.r_ohm[0][1]
    inductance=np.full((n,n),p.unsaturated_h);inductance[0,0]+=external_l
    for j,c in enumerate(p.cages,1):inductance[j,j]+=c.leakage_h
    resistance=np.diag([p.stator_r_ohm+external_r,*[c.resistance_ohm for c in p.cages]])
    a=np.zeros((n+1,n+1),complex);a[:n,:n]=-np.linalg.solve(inductance,resistance)
    a[:n,n]=np.linalg.solve(inductance,np.eye(n)[:,0]);a[n,n]=1j*w
    x0=np.r_[np.zeros(n,complex),network.sources[0].ll_v*math.sqrt(2/3)]
    times=axis(settings);currents=np.array([(expm(a*t)@x0)[:n] for t in times]);psi=p.unsaturated_h*currents.sum(axis=1)
    c={};c['m.speed_rad_s']=[0.]*len(times)
    c['m.torque_nm']=(1.5*p.pole_pairs*(currents[:,0]*psi.conj()).imag).tolist()
    for j in range(n):
        prefix='m.' if j==0 else f'm.cage{j}_'
        for part,component in [('alpha',currents[:,j].real),('beta',currents[:,j].imag)]:c[prefix+'current_'+part+'_a']=component.tolist()
        flux=psi if j==0 else psi+p.cages[j-1].leakage_h*currents[:,j]
        prefix='m.stator_' if j==0 else f'm.cage{j}_'
        c[prefix+'flux_alpha_vs']=flux.real.tolist();c[prefix+'flux_beta_vs']=flux.imag.tolist()
    return dict(completed=True,time_s=times,channels=c,exact_augmented_matrix_exponential=True)


def compare(primary,reference):
    if not primary.get('completed') or not reference.get('completed'):raise ValueError('incomplete equipment traces')
    # Primary duplicates an event boundary. Physical states are continuous; use the last sample.
    index={t:i for i,t in enumerate(primary['time_s'])}
    if set(index)!=set(reference['time_s']):raise ValueError('sample axes differ')
    errors={};limits={}
    for key,wanted in reference['channels'].items():
        if key not in primary['channels']:raise ValueError('missing channel '+key)
        actual=np.array([primary['channels'][key][index[t]] for t in reference['time_s']]);wanted=np.array(wanted)
        if actual.shape!=wanted.shape or not np.isfinite(actual).all() or not np.isfinite(wanted).all():raise ValueError('invalid reference channels')
        errors[key]=float(np.max(abs(actual-wanted)))
        kind='temperature_c' if 'temperature' in key else 'current_a' if 'current' in key else 'flux_vs' if 'flux' in key else 'torque_nm' if 'torque' in key else 'speed_rad_s'
        limits[key]=LIMITS[kind]
    return dict(passed=all(errors[k]<=limits[k] for k in errors),errors=errors,limits=limits)


def run(out,wheel):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh output directory required')
    native_sources=verify_native(wheel);code=package_fingerprint();producer=sha256_file(Path(__file__))
    with zipfile.ZipFile(wheel) as archive:
        license_bytes=archive.read('motulator-0.19.0.dist-info/licenses/LICENSE')
    out.mkdir(parents=True,exist_ok=True);(out/'motulator-LICENSE').write_bytes(license_bytes)
    write_json(out/'protocol.json',dict(cases={n:dict(network=example(n)[0].model_dump(mode='json'),settings=example(n)[1].model_dump(mode='json')) for n in NAMES},limits=LIMITS,
        wheel_sha256=WHEEL_SHA256,native_source_sha256=native_sources,code_sha256=code,producer_sha256=producer,source=PUBLISHED,
        native_license_sha256=hashlib.sha256(license_bytes).hexdigest()))
    checks=[]
    for name in NAMES:
        print(name,flush=True);network,settings=example(name)
        reference=exact_reference(network,settings) if name.startswith('exact_') else native_reference(network,settings)
        write_json(out/name/'reference.json',reference)
        primary=run_flux_network(network,settings);write_json(out/name/'primary.json',primary)
        check=compare(primary,reference);write_json(out/name/'comparison.json',check);checks.append(check)
    if package_fingerprint()!=code or sha256_file(Path(__file__))!=producer:raise ValueError('producer changed during validation')
    result=dict(passed=all(c['passed'] for c in checks),cases=len(checks),code_sha256=code,producer_sha256=producer,
        limitations=['Native saturated comparison is single-bus/single-cage','Multi-cage exact reference is linear locked rotor','Thermal parameters are authored, not measured','No universal EMT or remote release qualification'],
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--wheel',type=Path,required=True);args=parser.parse_args()
    result=run(args.out,args.wheel);print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)

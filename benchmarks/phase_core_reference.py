"""Independent flux-coordinate RLC/multi-cage reference (no production RHS).

Finite subset: three ideal source phases, one three-phase PCC, three coupled
feed inductors, optional diagonal PCC capacitors, passive PCC resistors, and
isolated-star Gamma motors. solve_ivp DOP853 and original nodal KCL are used.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp


def source_values(case,t):
    result=[]
    for source in case['sources']:
        envelope=source['envelope'];scale=envelope[-1][1]
        for a,b in zip(envelope,envelope[1:]):
            if a[0]<=t<b[0]:scale=a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0]);break
        result.append(scale*sum((tone['rms'] if tone['order']==0 else math.sqrt(2)*tone['rms'])*math.cos(2*math.pi*case['frequency_hz']*tone['order']*t+tone['phase_rad']) for tone in source['tones']))
    return np.array(result)


def topology(case,t):
    changed={e['equipment_id']:e['value'] for e in sorted(case['events'],key=lambda e:e['time_s']) if e['time_s']<=t}
    load=[m for m in case['nodes'] if m not in {s['node'] for s in case['sources']}];idx={n:i for i,n in enumerate(load)};g=np.zeros((3,3))
    for r in case['resistors']:
        vector=np.zeros(3);vector[idx[r['frm']]]=1
        if r['to']!='0':vector[idx[r['to']]]-=1
        g+=np.outer(vector,vector)/changed.get(r['id'],r['resistance_ohm'])
    return g


def run_reference(case,times,max_step=5e-5,sides=None):
    known=[s['node'] for s in case['sources']];nodes=[n for n in case['nodes'] if n not in known]
    if len(known)!=3 or len(nodes)!=3 or case['converters'] or case['drives'] or case['injections']:raise ValueError('reference supports only declared three-phase RLC/motor subset')
    if any(e['kind']!='resistance' for e in case['events']):raise ValueError('reference supports only resistor events')
    if len(case['inductors'])!=3 or any((r['frm'],r['to'])!=(known[i],nodes[i]) or r['saturation_flux_vs'] for i,r in enumerate(case['inductors'])):raise ValueError('reference feed geometry differs')
    if any(m['nodes']!=nodes for m in case['motors']):raise ValueError('reference requires all isolated stars on its PCC')
    for r in case['resistors']:
        if r['frm'] not in nodes or r['to'] not in [*nodes,'0']:raise ValueError('unsupported reference resistor topology')
    C=np.zeros(3)
    for cap in case['capacitors']:
        if cap['frm'] not in nodes or cap['to']!='0':raise ValueError('reference supports diagonal PCC capacitance only')
        C[nodes.index(cap['frm'])]+=cap['capacitance_f']
    capacitive=bool(np.all(C>0))
    if np.any(C>0) and not capacitive:raise ValueError('reference rejects mixed-index capacitance')
    L=np.array(case['inductance_h']);R=np.array([r['resistance_ohm'] for r in case['inductors']]);cursor=6 if capacitive else 3;addresses=[]
    for motor in case['motors']:
        p=motor['passport'];n=1+len(p['cages']);size=2*n+1+(2 if p['thermal'] else 0);addresses.append((cursor,n));cursor+=size
    y=np.zeros(cursor+2)
    if capacitive:y[3:6]=[case['initial_voltage_v'].get(n,0.) for n in nodes]
    for motor,(at,n) in zip(case['motors'],addresses):
        p=motor['passport'];flux=motor.get('initial_fluxes_ab_vs')
        if flux is not None:y[at:at+2*n]=np.array(flux).ravel()
        elif motor.get('initial_currents_ab_a') is not None:raise ValueError('reference initialization requires explicit fluxes')
        y[at+2*n]=p['initial_speed_rad_s']
        if p['thermal']:y[at+2*n+1:at+2*n+3]=p['thermal']['ambient_c']
    transform=np.array([[1.,0.],[-.5,math.sqrt(3)/2],[-.5,-math.sqrt(3)/2]])
    inverse=2/3*transform.T;project=transform@inverse
    def evaluate(t,state):
        data=[];offset=np.zeros(3);conductance=np.zeros((3,3));g=topology(case,t)
        for motor,(at,n) in zip(case['motors'],addresses):
            p=motor['passport'];f=state[at:at+2*n].reshape(n,2);psi=f[:,0]+1j*f[:,1];ell=np.array([c['leakage_h'] for c in p['cages']]);ir=(psi[1:]-psi[0])/ell;thermal=p['thermal'];temp=state[at+2*n+1:at+2*n+3]
            factor=1+thermal['alpha_per_c']*(temp-thermal['reference_c']) if thermal else np.ones(2);rs=p['stator_r_ohm']*factor[0];rr=np.array([c['resistance_ohm'] for c in p['cages']])*factor[1]
            saturation=(p['saturation_beta_per_vs']*abs(psi[0]))**p['saturation_exponent'];magnetic=psi[0]*(1+saturation)/p['unsaturated_h']-sum(ir);gc=p.get('core_loss_s',0.);den=1+rs*gc
            offset+=transform@np.array([magnetic.real,magnetic.imag])/den;conductance+=gc/den*project;data.append((motor['id'],p,at,n,psi,ir,rs,rr,saturation,magnetic,gc,temp))
        v=state[3:6] if capacitive else np.linalg.solve(g+conductance,state[:3]-offset)
        voltage=complex(*(inverse@v));derivative=np.zeros_like(state);derivative[:3]=np.linalg.solve(L,source_values(case,t)-v-R*state[:3]);motor_current=np.zeros(3);info={};stored=float(.5*state[:3]@L@state[:3]+(.5*np.sum(C*v*v) if capacitive else 0.));loss=float(v@g@v+sum(R*state[:3]**2))
        for mid,p,at,n,psi,ir,rs,rr,saturation,magnetic,gc,temp in data:
            is_=(magnetic+gc*voltage)/(1+gc*rs);ds=voltage-rs*is_;w=state[at+2*n];rotor_rate=-rr*ir+1j*p['pole_pairs']*w*psi[1:];f=np.r_[ds,rotor_rate];derivative[at:at+2*n]=np.column_stack([f.real,f.imag]).ravel();torque=1.5*p['pole_pairs']*(magnetic*np.conj(psi[0])).imag;load=p['fan_nm_per_rad_s2']*w*abs(w)+p['viscous_nm_per_rad_s']*w
            derivative[at+2*n]=0. if p['locked_rotor'] else (torque-load)/p['inertia_kg_m2'];ps=1.5*rs*abs(is_)**2;pr=1.5*sum(rr*abs(ir)**2);pc=1.5*gc*abs(ds)**2
            stored+=1.5/p['unsaturated_h']*(abs(psi[0])**2/2+saturation*abs(psi[0])**2/(p['saturation_exponent']+2))+.75*sum(np.array([c['leakage_h'] for c in p['cages']])*abs(ir)**2)+.5*p['inertia_kg_m2']*w*w
            if p['thermal']:
                h=p['thermal'];cross=h['cross_w_per_k']*(temp[0]-temp[1]);hs=h['stator_ambient_w_per_k']*(temp[0]-h['ambient_c']);hr=h['rotor_ambient_w_per_k']*(temp[1]-h['ambient_c']);derivative[at+2*n+1:at+2*n+3]=[(ps+pc-hs-cross)/h['stator_capacity_j_per_k'],(pr-hr+cross)/h['rotor_capacity_j_per_k']];stored+=h['stator_capacity_j_per_k']*(temp[0]-h['ambient_c'])+h['rotor_capacity_j_per_k']*(temp[1]-h['ambient_c']);loss+=hs+hr
            else:loss+=ps+pr+pc
            loss+=load*w;motor_current+=transform@np.array([is_.real,is_.imag]);info[mid]=dict(current_alpha_a=is_.real,current_beta_a=is_.imag,motor_speed_rad_s=w,torque_nm=torque,core_loss_w=pc,rotor_copper_w=pr,stator_copper_w=ps,stator_flux_alpha_vs=psi[0].real,stator_flux_beta_vs=psi[0].imag)
            if p['thermal']:info[mid].update(stator_temperature_c=temp[0],rotor_temperature_c=temp[1])
        if capacitive:derivative[3:6]=(state[:3]-g@v-motor_current)/C
        derivative[-2:]=[source_values(case,t)@state[:3],loss]
        return derivative,v,info,stored
    duration=float(max(times));boundaries=sorted({duration,*[e['time_s'] for e in case['events']],*[p[0] for s in case['sources'] for p in s['envelope'] if p[0]>0]});solutions=[];left=0.
    for end in boundaries:
        if end>duration:continue
        # Endpoint resistance changes are represented by the next segment.
        def rhs(t,x):return evaluate(np.nextafter(end,-np.inf) if t>=end and end<duration else t,x)[0]
        sol=solve_ivp(rhs,(left,end),y,method='DOP853',rtol=1e-11,atol=1e-12,max_step=max_step,dense_output=True)
        if not sol.success:raise ValueError('independent phase-core integration failed')
        solutions.append((left,end,sol));y=sol.y[:,-1];left=end
    records=[];initial_stored=evaluate(0.,solutions[0][2].sol(0.))[3]
    for index,t in enumerate(times):
        sol=next(s for a,b,s in solutions if a-1e-14<=t<=b+1e-14);state=sol.sol(t);sample_t=np.nextafter(t,-np.inf) if sides is not None and sides[index]=='pre_event' else t;_,v,info,stored=evaluate(sample_t,state);row={n+'.voltage_v':float(v[i]) for i,n in enumerate(nodes)};row.update({b['id']+'.current_a':float(state[i]) for i,b in enumerate(case['inductors'])})
        for motor in case['motors']:
            for key,value in info[motor['id']].items():row[motor['id']+'.'+key]=float(value)
        records.append(dict(channels=row,energy_error_j=float(state[-2]-state[-1]-(stored-initial_stored))))
    return dict(records=records,coordinate_system='all machines flux-state; original 3-phase RLC KCL',primary_rhs_used=False)

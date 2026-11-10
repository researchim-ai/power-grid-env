"""Independent all-flux drive and 3-phase RLC ODE, no production RHS/control calls.

Same declared averaged controller policy; not an independently certified OEM drive.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp


def run_reference(case,times,sides,max_step=2.5e-5):
    if not times or len(times)>5000 or not 0<max(times)<=2 or min(times)<0 or any(a>b for a,b in zip(times,times[1:])) or len(sides)!=len(times):raise ValueError('bounded reference time inventory required')
    if len(case['drives'])!=1 or case['motors'] or case['converters'] or case['injections']:raise ValueError('one averaged drive RLC reference only')
    d=case['drives'][0];passive=d.get('diode_front_end');p=d['motor'];nodes=list(d['nodes']);known=[s['node'] for s in case['sources']]
    if len(known)!=3 or set(case['nodes'])!=set(nodes+known) or len(case['inductors'])!=3:raise ValueError('reference geometry differs')
    if any((b['frm'],b['to'])!=(known[i],nodes[i]) or b['saturation_flux_vs'] for i,b in enumerate(case['inductors'])):raise ValueError('unsupported reference feed')
    g=np.zeros((3,3));C=np.zeros(3)
    for r in case['resistors']:
        if r['frm'] not in nodes or r['to']!='0':raise ValueError('reference supports shunt resistors only')
        g[nodes.index(r['frm']),nodes.index(r['frm'])]+=1/r['resistance_ohm']
    for c in case['capacitors']:
        if c['frm'] not in nodes or c['to']!='0':raise ValueError('reference supports diagonal PCC capacitance')
        C[nodes.index(c['frm'])]+=c['capacitance_f']
    if np.any(C<=0) or any(e['kind'] not in {'converter_connection','drive_precharge_bypass'} or e['equipment_id']!=d['id'] for e in case['events']):raise ValueError('unsupported reference capacitance/event')
    L=np.array(case['inductance_h']);R=np.array([b['resistance_ohm'] for b in case['inductors']]);n=1+len(p['cages']);loop=d.get('current_control');m=15 if loop else 13;speed=m+2*n;end=speed+1+(2 if p['thermal'] else 0)
    y=np.zeros(end+2);y[3:6]=[case['initial_voltage_v'].get(node,0.) for node in nodes];y[6]=d['phase_rad'];y[10]=.5*d['dc_capacitance_f']*d['initial_dc_v']**2;y[speed]=p['initial_speed_rad_s']
    if passive:y[10]=d['initial_dc_v']
    if d.get('motor_initial_fluxes_ab_vs') is not None:y[m:speed]=np.array(d['motor_initial_fluxes_ab_vs']).ravel()
    if p['thermal']:y[speed+1:end]=p['thermal']['ambient_c']
    T=np.array([[1.,0.],[-.5,math.sqrt(3)/2],[-.5,-math.sqrt(3)/2]]);ab=2/3*T.T;ell=np.array([r['leakage_h'] for r in p['cages']]);rr0=np.array([r['resistance_ohm'] for r in p['cages']])
    def source(t):
        v=[]
        for s in case['sources']:
            amplitude=s['envelope'][-1][1]
            for left,right in zip(s['envelope'],s['envelope'][1:]):
                if left[0]<=t<right[0]:amplitude=left[1]+(right[1]-left[1])*(t-left[0])/(right[0]-left[0]);break
            v.append(amplitude*sum((q['rms'] if q['order']==0 else math.sqrt(2)*q['rms'])*math.cos(2*math.pi*case['frequency_hz']*q['order']*t+q['phase_rad']) for q in s['tones']))
        return np.array(v)
    def evaluate(t,x):
        enabled=d['connected'];bypassed=passive['precharge_bypassed'] if passive else False
        for event in sorted(case['events'],key=lambda e:e['time_s']):
            if t>=event['time_s']:
                if event['kind']=='drive_precharge_bypass':bypassed=bool(event['value'])
                else:enabled=bool(event['value'])
        theta,integral,id_,iq,energy,dc_integral,angle=x[6:13];v=complex(*(ab@x[3:6]));vdc=math.sqrt(max(0.,2*energy/d['dc_capacitance_f']))
        if passive:vdc=max(0.,energy);energy=.5*d['dc_capacitance_f']*vdc*vdc
        if x[10] < -1e-5:raise ValueError(f'independent DC energy left domain: t={t}, dc={x[10]}, passive={bool(passive)}')
        frequency=d['motor_frequency_hz']*min(max(t/d['ramp_s'],0.),1.);wanted_v=math.sqrt(2)*d['motor_ln_rms_v']*frequency/d['motor_frequency_hz'];scale=min(1.,max(0.,(vdc/d['dc_reference_v']-.2)/.1));wanted_v*=scale;available=.5*vdc*d['maximum_modulation'];u=min(wanted_v,available)*complex(math.cos(angle),math.sin(angle))
        flux=x[m:speed].reshape(n,2);psi=flux[:,0]+1j*flux[:,1];ir=(psi[1:]-psi[0])/ell;temp=x[speed+1:end]
        factors=1+p['thermal']['alpha_per_c']*(temp-p['thermal']['reference_c']) if p['thermal'] else np.ones(2);rs=p['stator_r_ohm']*factors[0];rr=rr0*factors[1];sat=(p['saturation_beta_per_vs']*abs(psi[0]))**p['saturation_exponent'];mag=psi[0]*(1+sat)/p['unsaturated_h']-sum(ir);gc=p.get('core_loss_s',0.)
        loop_info={};loop_rate=0j
        if loop:
            rotation=complex(math.cos(angle),math.sin(angle));target=complex(loop['direct_peak_a'],loop['quadrature_peak_a'])*min(max(t/loop['ramp_s'],0.),1.)
            # Reference PI is integrated in dq, unlike the production stationary
            # integrator. Solve core feedthrough explicitly in that frame.
            z=complex(x[13],x[14]);offset=mag/(1+rs*gc)/rotation;admittance=gc/(1+rs*gc)
            command=(loop['kp_ohm']*(target-offset)+z)/(1+loop['kp_ohm']*admittance);bound=available*scale
            udq=command*min(1.,bound/max(abs(command),1e-300));actual_dq=offset+admittance*udq
            error_i=target-actual_dq;loop_rate=loop['ki_ohm_per_s']*error_i+loop['antiwindup_per_s']*(udq-loop['kp_ohm']*error_i-z)
            u=udq*rotation;ref_i=target*rotation
            loop_info=dict(current_reference_alpha_a=ref_i.real,current_reference_beta_a=ref_i.imag,current_tracking_error_a=abs(error_i),inverter_voltage_alpha_v=u.real,inverter_voltage_beta_v=u.imag,current_control_limited=abs(command)>bound)
        current=(mag+gc*u)/(1+rs*gc);ds=u-rs*current;pm=1.5*(u*np.conj(current)).real
        pdci=pm/d['inverter_efficiency'] if pm>=0 else pm*d['inverter_efficiency'];bleed=vdc*vdc/d['bleed_ohm'];duty=0. if d.get('braking_resistance_ohm') is None else min(1.,max(0.,(vdc-d['braking_start_v'])/(d['braking_full_v']-d['braking_start_v'])));brake=0. if d.get('braking_resistance_ohm') is None else duty*vdc*vdc/d['braking_resistance_ohm'];error=.5*d['dc_capacitance_f']*d['dc_reference_v']**2-energy;dc_request=pdci+bleed+brake+d['dc_kp_per_s']*error+dc_integral;grid_request=dc_request/d['rectifier_efficiency'] if dc_request>=0 else dc_request*d['rectifier_efficiency'];setpoint=-grid_request/(1.5*max(abs(v),.1*math.sqrt(2)*d['grid_ln_rms_v']));limit=math.sqrt(2)*d['grid_current_limit_rms_a'];limited=abs(setpoint)>limit;setpoint=min(limit,max(-limit,setpoint)) if enabled else 0.
        actual=complex(id_,iq)*complex(math.cos(theta),math.sin(theta)) if enabled else 0j;pg=-1.5*(v*np.conj(actual)).real;pdcr=pg*d['rectifier_efficiency'] if pg>=0 else pg/d['rectifier_efficiency'];phase_error=(v*complex(math.cos(theta),-math.sin(theta))).imag/max(abs(v),.1*math.sqrt(2)*d['grid_ln_rms_v']);correction=d['pll_kp_per_s']*phase_error+integral;cap=2*math.pi*5
        extra={}
        if passive:
            terminal=x[3:6]
            resistance=passive['series_resistance_ohm']+(0. if bypassed else passive['precharge_resistance_ohm'])
            dc_current=0.;abc=np.zeros(3)
            if enabled and np.ptp(terminal)>vdc+2*passive['diode_drop_v']:
                from itertools import combinations
                found=False
                for nh,nl in ((1,1),(1,2),(2,1)):
                    for hi in combinations(range(3),nh):
                        for lo in combinations([i for i in range(3) if i not in hi],nl):
                            # Solve the two rail KCL equations and DC branch KVL.
                            matrix=[[nh,0.,0.], [0.,nl,0.], [1.,-1.,-resistance]]
                            matrix[0][2]=passive['diode_resistance_ohm'];matrix[1][2]=-passive['diode_resistance_ohm']
                            vp,vn,idc=np.linalg.solve(matrix,[sum(terminal[list(hi)])-nh*passive['diode_drop_v'],sum(terminal[list(lo)])+nl*passive['diode_drop_v'],vdc])
                            up=np.maximum(terminal-vp-passive['diode_drop_v'],0.)/passive['diode_resistance_ohm'];down=np.maximum(vn-terminal-passive['diode_drop_v'],0.)/passive['diode_resistance_ohm']
                            if idc>=0 and max(abs(sum(up)-idc),abs(sum(down)-idc))<1e-7*(1+idc):
                                abc=down-up;dc_current=idc;found=True;break
                        if found:break
                    if found:break
                if not found:raise ValueError('independent six-diode circuit has no passive solution')
            actual=complex(*(ab@abc));pg=-terminal@abc;pdcr=vdc*dc_current;limited=False
            extra=dict(rectifier_current_a=dc_current,precharge_loss_w=0. if bypassed else passive['precharge_resistance_ohm']*dc_current**2,precharge_bypassed=bypassed,rectifier_conducting=dc_current>0.)
        dx=np.zeros_like(x);dx[:3]=np.linalg.solve(L,source(t)-x[3:6]-R*x[:3]);dx[3:6]=(x[:3]-g@x[3:6]+T@np.array([actual.real,actual.imag]))/C
        dx[6:13]=[2*math.pi*d['grid_frequency_hz']+min(cap,max(-cap,correction)),d['pll_ki_per_s2']*phase_error if abs(correction)<cap or correction*phase_error<0 else 0.,(setpoint-id_)/d['rectifier_tau_s'],-iq/d['rectifier_tau_s'],pdcr-pdci-bleed-brake,d['dc_ki_per_s2']*error if enabled and (not limited or error*grid_request<0) else 0.,2*math.pi*frequency]
        if passive:
            dx[6:10]=0.;dx[11]=0.
            dx[10]=(dc_current-(pdci/vdc if vdc else 0.)-vdc/d['bleed_ohm']-(brake/vdc if vdc else 0.))/d['dc_capacitance_f']
        if loop:dx[13:15]=[loop_rate.real,loop_rate.imag]
        rotor_rate=-rr*ir+1j*p['pole_pairs']*x[speed]*psi[1:];rate=np.r_[ds,rotor_rate];dx[m:speed]=np.column_stack([rate.real,rate.imag]).ravel();torque=1.5*p['pole_pairs']*(mag*np.conj(psi[0])).imag;load=p['fan_nm_per_rad_s2']*x[speed]*abs(x[speed])+p['viscous_nm_per_rad_s']*x[speed];dx[speed]=0. if p['locked_rotor'] else (torque-load)/p['inertia_kg_m2'];ps=1.5*rs*abs(current)**2;pr=1.5*sum(rr*abs(ir)**2);pc=1.5*gc*abs(ds)**2;mechanical=load*x[speed]
        motor_stored=1.5/p['unsaturated_h']*(abs(psi[0])**2/2+sat*abs(psi[0])**2/(p['saturation_exponent']+2))+.75*sum(ell*abs(ir)**2)+.5*p['inertia_kg_m2']*x[speed]**2;motor_loss=ps+pr+pc
        if p['thermal']:
            h=p['thermal'];cross=h['cross_w_per_k']*(temp[0]-temp[1]);hs=h['stator_ambient_w_per_k']*(temp[0]-h['ambient_c']);hr=h['rotor_ambient_w_per_k']*(temp[1]-h['ambient_c']);dx[speed+1:end]=[(ps+pc-hs-cross)/h['stator_capacity_j_per_k'],(pr-hr+cross)/h['rotor_capacity_j_per_k']];motor_stored+=h['stator_capacity_j_per_k']*(temp[0]-h['ambient_c'])+h['rotor_capacity_j_per_k']*(temp[1]-h['ambient_c']);motor_loss=hs+hr
        rect_loss=pg-pdcr;inv_loss=pdci-pm;loss=rect_loss+inv_loss+bleed+brake+motor_loss;stored=energy+motor_stored+.5*x[:3]@L@x[:3]+.5*sum(C*x[3:6]**2);dx[-2:]=[source(t)@x[:3],x[3:6]@g@x[3:6]+sum(R*x[:3]**2)+loss+mechanical]
        info=dict(braking_duty=duty,braking_power_w=brake,dc_voltage_v=vdc,dc_energy_j=energy,motor_speed_rad_s=x[speed],motor_current_peak_a=abs(current),motor_power_w=pm,grid_power_w=pg,rectifier_dc_power_w=pdcr,inverter_dc_power_w=pdci,rectifier_loss_w=rect_loss,inverter_loss_w=inv_loss,bleed_loss_w=bleed,motor_stored_j=motor_stored,motor_core_loss_w=pc,motor_stator_copper_w=ps,motor_rotor_copper_w=pr,motor_torque_nm=torque,motor_current_alpha_a=current.real,motor_current_beta_a=current.imag,motor_stator_flux_alpha_vs=psi[0].real,motor_stator_flux_beta_vs=psi[0].imag,current_vector_peak_a=abs(actual),inverter_frequency_hz=frequency,stored_j=energy+motor_stored,loss_w=loss,mechanical_power_w=mechanical,undervoltage_derated=scale<1.,modulation_limited=wanted_v>available,current_limited=limited,connected=enabled)
        info.update(extra)
        info.update(loop_info)
        if loop:info['modulation_limited']=loop_info['current_control_limited']
        if p['thermal']:info.update(motor_stator_temperature_c=temp[0],motor_rotor_temperature_c=temp[1])
        row={node+'.voltage_v':float(x[3+i]) for i,node in enumerate(nodes)};row.update({b['id']+'.current_a':float(x[i]) for i,b in enumerate(case['inductors'])});row.update({d['id']+'.'+key:float(value) for key,value in info.items()})
        return dx,row,float(stored)
    duration=float(max(times));boundaries=sorted({duration,*[e['time_s'] for e in case['events'] if e['time_s']<=duration],*[t for s in case['sources'] for t,_ in s['envelope'] if 0<t<=duration]});solutions=[];left=0.
    for end_time in boundaries:
        def rhs(t,state):return evaluate(np.nextafter(end_time,-np.inf) if t>=end_time and end_time<duration else t,state)[0]
        sol=solve_ivp(rhs,(left,end_time),y,method='DOP853',rtol=1e-11,atol=1e-12,max_step=max_step,dense_output=True)
        if not sol.success:raise ValueError('independent drive integration failed')
        solutions.append((left,end_time,sol));y=sol.y[:,-1];left=end_time
    records=[];initial=evaluate(0.,solutions[0][2].sol(0.))[2]
    for t,side in zip(times,sides,strict=True):
        sol=next(s for a,b,s in solutions if a-1e-14<=t<=b+1e-14);state=sol.sol(t);_,row,stored=evaluate(np.nextafter(t,-np.inf) if side=='pre_event' else t,state);records.append(dict(channels=row,energy_error_j=float(state[-2]-state[-1]-(stored-initial))))
    return dict(records=records,time_s=times,sample_side=sides,primary_rhs_used=False,scope='separate all-flux machine + explicit RLC + authored averaged controller')

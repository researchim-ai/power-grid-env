"""SI oracle with exact restriction of isolated unexcited zero rows.

Derived from closed_loop_protection_reference's conditioned motor-EMF oracle.
No fictitious conductance; nonzero current into an empty row is rejected.
This remains a conditional algebraic reference, not an independent trajectory.
"""
import cmath
import math
import numpy as np
from power_grid_env.topology import compile_topology

def algebra(case,report):
    n=case.study.network;s=case.study.coupled
    top=compile_topology(n);source=n.sources[0];line=n.lines[0]
    upstream=top.terminal_bus[source.terminal_id];downstream=top.node_to_bus[s.motors[0].node_id]
    ll=n.voltage_levels[0].nominal_ll_v;vbase=ll/math.sqrt(3)
    sourcez=complex(source.r_ohm,source.x_ohm);linez=complex(line.r_ohm[0][0],line.x_ohm[0][0])
    load_y=sum(complex(sum(l.p_w.values()),-sum(l.q_var.values()))/ll**2 for l in n.loads)
    motorz=[]
    for m in s.motors:
        xp=m.stator_x_pu+m.rotor_x_pu*m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)
        motorz.append(complex(m.stator_r_pu,xp)*m.rated_ll_v**2/m.rated_va)
    ch=report['channels'];errors=dict(voltage_pu=0.,relay_current_a=0.,fault_current_a=0.)
    for i,(t,side) in enumerate(zip(report['time_s'],report['sample_side'],strict=True)):
        scale=1.
        for e in sorted(s.events,key=lambda e:e.time_s):
            if e.kind=='source_voltage' and (t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event'):scale=e.value
        esource=cmath.rect(source.ll_v/math.sqrt(3)*scale,source.angle_a_rad)
        ys=ch[source.id+'.connected'][i]/sourcez;yl=ch[line.id+'.closed'][i]/linez
        ym=0j;im=0j
        for m,z in zip(s.motors,motorz):
            y=ch[m.id+'.connected'][i]/z
            emf=complex(ch[m.id+'.e_real_pu'][i],ch[m.id+'.e_imag_pu'][i])*m.rated_ll_v/math.sqrt(3)
            ym+=y;im+=emf*y
        yf=sum(ch[f.id+'.active'][i]/complex(f.r_ohm,f.x_ohm) for f in s.faults)
        matrix=np.array([[ys+yl,-yl],[-yl,yl+ym+load_y+yf]],complex)
        rhs=np.array([ys*esource,im]);values=np.zeros(2,complex)
        # Exactly empty disconnected rows carry no excitation. Do not add
        # a fictitious shunt or discard a nonzero injection into a dead bus.
        live=np.flatnonzero(np.any(matrix!=0,axis=1))
        dead=np.setdiff1d(np.arange(2),live)
        if np.any(rhs[dead]!=0):raise ValueError('excited zero-admittance reference node')
        if len(live):values[live]=np.linalg.solve(matrix[np.ix_(live,live)],rhs[live])
        vu,vd=values
        iline=abs(yl*(vu-vd));isource=abs(ys*(esource-vu))
        for bus,value in [(upstream,vu),(downstream,vd)]:
            actual=cmath.rect(ch[bus+'.v'][i]*vbase,ch[bus+'.a'][i])
            errors['voltage_pu']=max(errors['voltage_pu'],abs(value-actual)/vbase)
        for relay in s.network_automation.relays:
            available=relay.measurement_available
            for e in sorted(s.events,key=lambda e:e.time_s):
                if e.equipment_id==relay.id and e.kind in ['relay_measurement_lost','relay_measurement_restored'] and (t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event'):
                    available=e.kind=='relay_measurement_restored'
            expected=(iline if relay.measurement_kind=='line' else isource) if available else 0.
            errors['relay_current_a']=max(errors['relay_current_a'],abs(expected-ch[relay.id+'.measurement'][i]))
        for f in s.faults:
            expected=abs(vd/complex(f.r_ohm,f.x_ohm))*ch[f.id+'.active'][i]
            errors['fault_current_a']=max(errors['fault_current_a'],abs(expected-ch[f.id+'.current_a'][i]))
    limits=dict(voltage_pu=2e-8,relay_current_a=2e-5,fault_current_a=2e-5)
    return dict(passed=all(errors[k]<=limits[k] for k in limits),errors=errors,limits=limits,samples=len(report['time_s']))

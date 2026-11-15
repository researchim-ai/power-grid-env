"""Independent cold-start normal-mode trajectories in physical winding fluxes.

Three SI winding/KCL equations and rotor-flux dynamics, DOP853. Production
motor derivatives, Norton stamps, constants, initial states and EMF traces
are never used to advance the reference. Only native times are used for
comparison sampling. Active relay/thermal/UV protection is outside this oracle.
"""
import cmath
import math
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor,lu_solve
from power_grid_env.study_spec import StudySpec
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest

LIMITS=dict(speed_pu=2e-6,emf_pu=2e-6,current_pu=5e-6,bus_voltage_pu=2e-6,thermal_level=5e-6)


def reference(study,times,sides):
    spec=StudySpec.model_validate(study);n=spec.network;s=spec.coupled
    if s is None or s.machines or s.faults or s.operating_point or len(s.motors)!=1 or len(n.lines)!=1 or len(n.sources)!=1:
        raise ValueError('reference requires cold motor-only normal radial mode')
    if n.generators or n.transformers or n.switches or n.shunts or n.inverters or n.storages:
        raise ValueError('additional equipment outside winding reference')
    m=s.motors[0];source=n.sources[0];line=n.lines[0]
    if m.model!='single_cage' or m.mechanical_load is not None:raise ValueError('single cage and authored fan mechanism required')
    terminals={t.id:t.connectivity_node_id for t in n.terminals}
    if terminals[line.terminal_from]!=terminals[source.terminal_id] or terminals[line.terminal_to]!=m.node_id:
        raise ValueError('oriented radial feeder required')
    if np.any(line.b_siemens) or source.phase_pu is not None:raise ValueError('balanced source and zero line charging required')
    for load in n.loads:
        if load.model!='constant_z' or terminals[load.terminal_id]!=m.node_id:raise ValueError('downstream constant-Z load required')
        if any(len(set(values.values()))!=1 for values in [load.p_w,load.q_var]):raise ValueError('balanced phase powers required')
    if any(e.kind not in ('source_voltage','motor_open','motor_close') for e in s.events):raise ValueError('only authored normal-mode commands supported')
    if any(e.kind=='source_voltage' and e.equipment_id!=source.id or e.kind.startswith('motor_') and e.equipment_id!=m.id for e in s.events):raise ValueError('unknown normal-mode event target')
    automation=s.network_automation
    if automation is None or automation.transfers or any(b.initial_open for b in automation.breakers):raise ValueError('initially connected, no transfer reference required')
    if any(r.kind!='overcurrent' or r.measurement_kind not in ('source','line') or not r.measurement_available for r in automation.relays):raise ValueError('available line/source overcurrent CTs required')
    if m.contactor and m.contactor.maximum_automatic_restarts!=0:raise ValueError('automatic restart outside independent reference')
    wb=2*math.pi*n.frequency_hz;zb=m.rated_ll_v**2/m.rated_va;vb=m.rated_ll_v/math.sqrt(3);ib=m.rated_va/(3*vb)
    rs=m.stator_r_pu*zb;rr=m.rotor_r_pu*zb
    lm=m.magnetizing_x_pu*zb/wb;ls=(m.stator_x_pu+m.magnetizing_x_pu)*zb/wb;lr=(m.rotor_x_pu+m.magnetizing_x_pu)*zb/wb
    zs=complex(source.r_ohm,source.x_ohm)
    zl=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1]);feed=zs+zl
    load_y=sum(complex(sum(l.p_w.values()),-sum(l.q_var.values())) for l in n.loads)/m.rated_ll_v**2
    es=cmath.rect(source.ll_v/math.sqrt(3),source.angle_a_rad)
    matrices={}
    for on in [False,True]:
        matrix=np.array([[rs+1j*wb*ls,1j*wb*lm,-1],[lm,lr,0],[1,0,load_y+1/feed]],complex)
        if not on:matrix[0]=[1,0,0]
        matrices[on]=lu_factor(matrix)
    def circuit(x,on,scale):
        flux=complex(x[0],x[1]);im,ir,voltage=lu_solve(matrices[on],np.array([0,flux,es*scale/feed]))
        grid_current=(es*scale-voltage)/feed;upstream=es*scale-zs*grid_current
        return im,ir,voltage,upstream,grid_current
    connected=m.connected;scale=1.;segments=[];state=np.array([0.,0.,0.,m.thermal.initial_level if m.thermal else 0.])
    events=sorted(s.events,key=lambda e:e.time_s);boundaries=sorted({0.,s.duration_s,*[e.time_s for e in events]})
    def safe(x,on,scale):
        im,ir,voltage,_,grid=circuit(x,on,scale)
        if any(abs(grid)>=r.pickup for r in automation.relays):raise ValueError('active overcurrent outside winding reference')
        if on and m.contactor and abs(voltage)/vb<=m.contactor.dropout_voltage_pu:raise ValueError('UV action outside winding reference')
        if m.thermal and x[3]>=m.thermal.trip_level:raise ValueError('thermal action outside winding reference')
    for left,right in zip(boundaries,boundaries[1:]):
        for event in [e for e in events if e.time_s==left]:
            if event.kind=='source_voltage':scale=event.value
            elif event.kind=='motor_open':connected=False
            else:
                if m.contactor and abs(circuit(state,False,scale)[2])/vb<m.contactor.pickup_voltage_pu:raise ValueError('blocked manual close outside winding reference')
                connected=True
        safe(state,connected,scale)
        def derivative(t,x):
            im,ir,voltage,_,grid=circuit(x,connected,scale)
            flux=complex(x[0],x[1]);df=-rr*ir-1j*wb*(1-x[2])*flux
            torque=-3*wb*lm*(ir*np.conj(im)).imag/m.rated_va
            heat=((abs(im)/ib)**2-x[3])/m.thermal.time_constant_s if m.thermal else 0.
            return [df.real,df.imag,(torque-m.fan_torque_pu*x[2]*abs(x[2]))/(2*m.inertia_s),heat]
        guards=[]
        for relay in automation.relays:
            def overcurrent(t,x,pickup=relay.pickup):return pickup-abs(circuit(x,connected,scale)[4])
            overcurrent.terminal=True;overcurrent.direction=-1;guards.append(overcurrent)
        if connected and m.contactor:
            def uv(t,x):return abs(circuit(x,connected,scale)[2])/vb-m.contactor.dropout_voltage_pu
            uv.terminal=True;uv.direction=-1;guards.append(uv)
        if m.thermal:
            def thermal(t,x):return m.thermal.trip_level-x[3]
            thermal.terminal=True;thermal.direction=-1;guards.append(thermal)
        solved=solve_ivp(derivative,(left,right),state,method='DOP853',rtol=1e-11,atol=1e-12,dense_output=True,events=guards)
        if not solved.success or abs(solved.t[-1]-right)>1e-10:raise ValueError('reference failed or reached unmodeled protective action')
        segments.append((left,right,solved.sol,connected,scale));state=solved.y[:,-1];safe(state,connected,scale)
    output=[]
    for t,side in zip(times,sides,strict=True):
        segment=next((v for v in segments if v[0]-1e-10<=t<=v[1]+1e-10),None)
        if segment is None:raise ValueError('native time outside reference coverage')
        x=segment[2](t);on=m.connected;factor=1.
        for e in events:
            if t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event':
                if e.kind=='source_voltage':factor=e.value
                else:on=e.kind=='motor_close'
        im,ir,v,up,grid=circuit(x,on,factor);emf=1j*wb*lm/lr*complex(x[0],x[1])/vb
        output.append(dict(time_s=float(t),sample_side=side,rotor_flux_real_vs=float(x[0]),rotor_flux_imag_vs=float(x[1]),speed_pu=float(x[2]),thermal_level=float(x[3]),
                           emf_real_pu=float(emf.real),emf_imag_pu=float(emf.imag),current_pu=float(abs(im)/ib),
                           downstream_voltage_real_pu=float(v.real/vb),downstream_voltage_imag_pu=float(v.imag/vb),
                           upstream_voltage_real_pu=float(up.real/vb),upstream_voltage_imag_pu=float(up.imag/vb)))
    return output


def compare(study,report,values):
    s=StudySpec.model_validate(study);m=s.coupled.motors[0];n=s.network
    # The declared two-node cases have one bus per node; identify native bus
    # channel labels through the report mapping, not production network solves.
    from power_grid_env.topology import compile_topology
    top=compile_topology(n);down=top.node_to_bus[m.node_id];up=top.terminal_bus[n.sources[0].terminal_id]
    ch=report['channels'];errors={k:0. for k in LIMITS}
    if len(values)!=len(report['time_s']):raise ValueError('reference/native sample count differs')
    for i,row in enumerate(values):
        if abs(row['time_s']-report['time_s'][i])>1e-10 or row['sample_side']!=report['sample_side'][i]:raise ValueError('reference/native event-sided axes differ')
        errors['speed_pu']=max(errors['speed_pu'],abs(row['speed_pu']-ch[m.id+'.speed_pu'][i]))
        errors['emf_pu']=max(errors['emf_pu'],abs(complex(row['emf_real_pu'],row['emf_imag_pu'])-complex(ch[m.id+'.e_real_pu'][i],ch[m.id+'.e_imag_pu'][i])))
        errors['current_pu']=max(errors['current_pu'],abs(row['current_pu']-ch[m.id+'.current_pu'][i]))
        if m.thermal:errors['thermal_level']=max(errors['thermal_level'],abs(row['thermal_level']-ch[m.id+'.thermal_level'][i]))
        for label,bus in [('upstream',up),('downstream',down)]:
            actual=cmath.rect(ch[bus+'.v'][i],ch[bus+'.a'][i]);expected=complex(row[label+'_voltage_real_pu'],row[label+'_voltage_imag_pu'])
            errors['bus_voltage_pu']=max(errors['bus_voltage_pu'],abs(actual-expected))
    return dict(passed=all(errors[k]<=LIMITS[k] for k in LIMITS),errors=errors,limits=LIMITS,samples=len(values))


def qualify(store,interior,out):
    store=Path(store);interior=Path(interior);a=verify(store/'index');b=verify(interior/'index')
    if not read_json(store/'index/result.json')['passed'] or not read_json(interior/'index/result.json')['passed']:raise ValueError('qualified native sources required')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[]
    prefixes=[(str(p.parent.name),p) for p in sorted((store/'conditions').glob('*/nominal'))]
    prefixes.append(('center',interior/'points/center'))
    for label,parent in prefixes:
        parent_anchor=verify(parent)
        for mode in ['start','repeated_sag','coast_restart']:
            dae=parent/f'normal-{mode}-network_dae-grid2';radau=parent/f'normal-{mode}-radau-grid2'
            study=read_json(dae/'study.json');native=read_json(dae/'trace.json')
            values=reference(study,native['time_s'],native['sample_side'])
            checks=dict(dae=compare(study,native,values),radau=compare(read_json(radau/'study.json'),read_json(radau/'trace.json'),values))
            name=label+'-'+mode;child=root/name;child.mkdir()
            (child/'reference.json').write_bytes(encoded(values));(child/'checks.json').write_bytes(encoded(checks));seal(child)
            rows.append(dict(id=name,passed=all(c['passed'] for c in checks.values()),checks=checks))
            print('winding reference',name,rows[-1]['passed'],flush=True)
        if verify(parent)!=parent_anchor:raise ValueError('native source changed during winding reference')
    if verify(store/'index')!=a or verify(interior/'index')!=b:raise ValueError('source index changed')
    result=dict(passed=len(rows)==15 and all(r['passed'] for r in rows),independent_trajectories=15,native_comparisons=30,rows=rows,
                source_indices=dict(cartesian=a,interior=b),producer_sha256=digest(Path(__file__).read_bytes()),
                scope='Independent SI stator/rotor winding KCL and rotor-flux ODE, DOP853, cold initial conditions;15 normal trajectories at four endpoint conditions and center, compared against both native fine-grid engines. Guards reject active OC/UV/thermal protection. Fault/protection trajectories are not independently qualified here.')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['store','interior','out']:p.add_argument('--'+key,required=True)
    r=qualify(**vars(p.parse_args()));print(dict(passed=r['passed'],independent_trajectories=15))
    if not r['passed']:raise SystemExit(2)

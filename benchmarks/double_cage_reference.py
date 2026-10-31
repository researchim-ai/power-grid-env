"""Independent physical SI winding circuit for mixed single/two-cage motors.

Common receiving bus behind one source/line; solve stator and every rotor
current simultaneously. Flux states and DOP853 differ from production EMF/Radau.
No production motor constants, winding reduction or network stamps are used.
"""
from pathlib import Path
import argparse,math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq,root
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.induction_reference import mechanical_torque_reference

KEYS=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
LIMITS=dict(zip(KEYS,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))


def reference(spec,report,*,hybrid_runner=None,generator_factory=None):
    n=spec.network;s=spec.coupled if generator_factory is not None else spec.induction;motors=s.motors;count=len(motors);wb=2*math.pi*n.frequency_hz
    if len(n.sources)!=1 or len(n.lines)!=1 or n.loads or (n.generators and generator_factory is None) or n.transformers or n.shunts or (any(m.contactor for m in motors) and hybrid_runner is None):raise ValueError('common-bus source/line fixtures without automatic controls required')
    if len({m.node_id for m in motors})!=1:raise ValueError('one receiving bus required')
    line=n.lines[0]
    if np.any(line.b_siemens):raise ValueError('no charging in physical reference')
    source=n.sources[0];feed=complex(source.r_ohm+line.r_ohm[0][0]-line.r_ohm[0][1],source.x_ohm+line.x_ohm[0][0]-line.x_ohm[0][1]);source_v=source.ll_v/math.sqrt(3)*np.exp(1j*source.angle_a_rad)
    parameters=[];rotor_count=0;state_end=0
    for m in motors:
        zb=m.rated_ll_v**2/m.rated_va;lm=m.magnetizing_x_pu*zb/wb
        rr=np.array([m.rotor_r_pu]+([m.rotor2_r_pu] if m.model=='double_cage' else []))*zb
        leakage=np.array([m.rotor_x_pu]+([m.rotor2_x_pu] if m.model=='double_cage' else []))*zb/wb
        k=len(rr);mutual=m.rotor_mutual_x_pu*zb/wb if k==2 else 0.
        matrix=(lm+mutual)*np.ones((k,k))+np.diag(leakage)
        parameters.append(dict(rs=m.stator_r_pu*zb,lm=lm,lss=lm+m.stator_x_pu*zb/wb,rr=rr,leakage=leakage,mutual=mutual,matrix=matrix,rotor=slice(rotor_count,rotor_count+k),state=slice(state_end,state_end+2*k),speed=state_end+2*k,vbase=m.rated_ll_v/math.sqrt(3),ibase=m.rated_va/(math.sqrt(3)*m.rated_ll_v)))
        rotor_count+=k;state_end+=2*k+1
    thermal={i:state_end+j for j,i in enumerate(i for i,m in enumerate(motors) if m.thermal)};dimension=state_end+len(thermal)
    connected=np.array([m.connected for m in motors]);latched=np.zeros(count,dtype=bool);scale=1.;load_scale=np.ones(count);matrix=None;cached_state=None;cached_result=None;generator=None;generator_slice=slice(dimension,dimension)
    def rebuild():
        nonlocal matrix,cached_state,cached_result
        cached_state=None;cached_result=None
        matrix=np.zeros((count+rotor_count+bool(generator),count+rotor_count+bool(generator)),dtype=complex)
        for i,p in enumerate(parameters):
            address=slice(count+p['rotor'].start,count+p['rotor'].stop)
            if connected[i]:
                matrix[i,:count]=feed*connected;matrix[i,i]+=p['rs']+1j*wb*p['lss'];matrix[i,address]=1j*wb*p['lm']
                if generator:matrix[i,-1]=-feed
            else:matrix[i,i]=1.
            # Scale flux-constraint rows from Wb to V. The same SI circuit
            # is solved, with better conditioning and preserved thermal ties.
            matrix[address,i]=wb*p['lm'];matrix[address,address]=wb*p['matrix']
        if generator:matrix[-1,:count]=feed*connected;matrix[-1,-1]=-feed-generator.z
    def fluxes(x):return [x[p['state']].reshape(-1,2)[:,0]+1j*x[p['state']].reshape(-1,2)[:,1] for p in parameters]
    def electrical(x):
        nonlocal cached_state,cached_result
        if cached_state is not None and np.array_equal(x,cached_state):return cached_result
        flux=fluxes(x);rhs=np.r_[source_v*scale*connected,wb*np.concatenate(flux)]
        if generator:rhs=np.r_[rhs,source_v*scale-generator.emf(x[generator_slice])]
        solution=np.linalg.solve(matrix,rhs);stator=solution[:count];rotor=solution[count:count+rotor_count];ig=solution[-1] if generator else 0j;voltage=source_v*scale-feed*(sum(stator)-ig)
        torque=np.array([-3*wb*p['lm']*(sum(rotor[p['rotor']])*np.conj(stator[i])).imag/m.rated_va for i,(m,p) in enumerate(zip(motors,parameters))])
        cached_state=np.array(x,copy=True);cached_result=(stator,rotor,voltage,torque,ig)
        return cached_result
    def rhs(time,x):
        stator,rotor,voltage,torque,ig=electrical(x);out=np.zeros(dimension)
        for i,(m,p,flux) in enumerate(zip(motors,parameters,fluxes(x))):
            derivative=-p['rr']*rotor[p['rotor']]-1j*wb*(1-x[p['speed']])*flux
            out[p['state']]=np.column_stack([derivative.real,derivative.imag]).ravel();out[p['speed']]=(torque[i]-load_scale[i]*mechanical_torque_reference(m,x[p['speed']]))/(2*m.inertia_s)
            if i in thermal:out[thermal[i]]=((abs(stator[i])/p['ibase'])**2-x[thermal[i]])/m.thermal.time_constant_s
        if generator:out[generator_slice]=generator.rhs(x[generator_slice],ig,voltage)
        return out
    state=np.zeros(dimension)
    def steady(speeds,forced_voltage=None):
        impedances=[];parallels=[];branches=[]
        for m,p,w in zip(motors,parameters,speeds):
            slip=1-w;zr=p['rr']/slip+1j*wb*p['leakage'] if slip else np.full(len(p['rr']),complex(np.inf))
            rotor_parallel=1/sum(1/zr) if slip else complex(np.inf);rotor_equivalent=rotor_parallel+1j*wb*p['mutual']
            parallel=1/(1/(1j*wb*p['lm'])+1/rotor_equivalent);impedances.append(p['rs']+1j*wb*(p['lss']-p['lm'])+parallel);parallels.append(parallel);branches.append((zr,rotor_parallel,rotor_equivalent))
        admittance=np.array([1/z if on else 0j for z,on in zip(impedances,connected)]);v=source_v/(1+feed*sum(admittance)) if forced_voltage is None else forced_voltage;stator=v*admittance
        rotor=[(-stator[i]*parallels[i]/branches[i][2])*branches[i][1]/branches[i][0] if speeds[i]!=1 else np.zeros(len(parameters[i]['rr']),dtype=complex) for i in range(count)];flux=[p['lm']*stator[i]+p['matrix']@rotor[i] for i,p in enumerate(parameters)]
        torque=np.array([-3*wb*p['lm']*(sum(rotor[i])*np.conj(stator[i])).imag/m.rated_va for i,(m,p) in enumerate(zip(motors,parameters))]);return flux,torque,stator
    if generator_factory is not None:
        def cold_current(voltage):
            currents=[]
            for p,on in zip(parameters,connected):
                reduced_l=p['lss']-p['lm']**2*sum(np.linalg.solve(p['matrix'],np.ones(len(p['rr']))))
                currents.append(voltage/complex(p['rs'],wb*reduced_l) if on else 0j)
            return np.array(currents)
        generator=generator_factory(n,s,feed,source_v,steady,cold_current)
        for i,p in enumerate(parameters):state[p['speed']]=generator.speeds[i]
        if s.operating_point:
            flux,_,stator=steady(generator.speeds,generator.voltage)
            for i,p in enumerate(parameters):state[p['state']]=np.column_stack([flux[i].real,flux[i].imag]).ravel()
        else:stator=cold_current(generator.voltage)
    else:
        if s.operating_point:
            active=np.flatnonzero(connected);speeds=np.zeros(count)
            def residual(values):
                speeds[active]=values;torque=steady(speeds)[1];return np.array([torque[i]-mechanical_torque_reference(motors[i],speeds[i]) for i in active])
            if len(active)==1:
                grid=np.linspace(s.operating_point.minimum_speed_pu,1.,501);roots=[]
                for lo,hi in zip(grid,grid[1:]):
                    if residual([lo])[0]*residual([hi])[0]<=0:roots.append(brentq(lambda w:residual([w])[0],lo,hi,xtol=1e-13))
                if not roots:raise ValueError('no independent high-speed root')
                speeds[active]=max(roots)
            else:
                solution=root(residual,np.full(len(active),.99),tol=1e-11)
                if not solution.success or max(abs(residual(solution.x)))>1e-8:raise ValueError('independent group equilibrium failed')
                speeds[active]=solution.x
            if np.any(speeds[active]<s.operating_point.minimum_speed_pu) or np.any(speeds[active]>1.):raise ValueError('independent equilibrium outside declared high-speed branch')
            flux,_,stator=steady(speeds)
            for i,p in enumerate(parameters):state[p['state']]=np.column_stack([flux[i].real,flux[i].imag]).ravel();state[p['speed']]=speeds[i]
        else:stator=np.zeros(count)
    for i,address in thermal.items():state[address]=(abs(stator[i])/parameters[i]['ibase'])**2 if s.operating_point and s.operating_point.thermal_state=='equilibrium' else motors[i].thermal.initial_level
    if generator:
        generator_slice=slice(dimension,dimension+len(generator.initial));state=np.r_[state,generator.initial];dimension=len(state)
    hybrid=None;changes=[]
    if hybrid_runner is not None:
        terminal_by_id={t.id:t for t in n.terminals}
        if any(set(terminal_by_id[tid].phases)!=set('ABC') for tid in [source.terminal_id,line.terminal_from,line.terminal_to,*[g.terminal_id for g in n.generators]]):raise ValueError('independent group requires three balanced phases')
        if not any(m.contactor for m in motors):raise ValueError('independent group requires a contactor')
        if n.switches or n.inverters or n.storages or n.neutral_grounds or n.study_faults or n.harmonics or len(n.connectivity_nodes)!=2:raise ValueError('unsupported independent group circuit')
        terminals={t.id:t.connectivity_node_id for t in n.terminals};source_node=terminals[source.terminal_id];motor_node=motors[0].node_id
        if generator and (terminals[generator.g.terminal_id]!=motor_node or generator.g.ll_v!=motors[0].rated_ll_v):raise ValueError('independent generator must share motor bus and voltage base')
        if {terminals[line.terminal_from],terminals[line.terminal_to]}!={source_node,motor_node}:raise ValueError('independent group feeder topology mismatch')
        if any(level.nominal_ll_v!=motors[0].rated_ll_v for level in n.voltage_levels) or any(m.rated_ll_v!=motors[0].rated_ll_v for m in motors):raise ValueError('independent group requires common nominal voltage base')
        for values in [line.r_ohm,line.x_ohm]:
            values=np.array(values);balanced=np.full((3,3),values[0,1]);np.fill_diagonal(balanced,values[0,0])
            if not np.allclose(values,balanced,rtol=0,atol=1e-12):raise ValueError('independent group requires balanced feeder')
        for m in motors:
            if m.contactor and m.contactor.sense_node_id not in {source_node,motor_node}:raise ValueError('independent group sensing node missing')
        for e in s.events:
            if e.kind not in {'source_voltage','load_torque_scale','motor_open','motor_close'}:raise ValueError('unsupported independent group event')
            if e.equipment_id not in ([source.id] if e.kind=='source_voltage' else [m.id for m in motors]):raise ValueError('independent group event target missing')
        def get_context():return dict(connected=connected.copy(),latched=latched.copy(),scale=scale,load_scale=load_scale.copy())
        def set_context(cfg):
            nonlocal connected,latched,scale,load_scale
            connected=cfg['connected'].copy();latched=cfg['latched'].copy();scale=cfg['scale'];load_scale=cfg['load_scale'].copy();rebuild()
        def sensed(x,i):
            stator,rotor,v,torque,ig=electrical(x)
            value=v if motors[i].contactor.sense_node_id==motor_node else source_v*scale-complex(source.r_ohm,source.x_ohm)*(sum(stator)-ig)
            return abs(value)/parameters[i]['vbase']
        def heating(x):return np.array([(abs(current)/p['ibase'])**2 for current,p in zip(electrical(x)[0],parameters)])
        rebuild()
        segments,changes,trips,hybrid=hybrid_runner(settings=s,state=state,rhs=rhs,voltage=sensed,heating=heating,thermal_indices=thermal,get_context=get_context,set_context=set_context)
    else:
        events={}
        for e in s.events:events.setdefault(e.time_s,[]).append(e)
        boundaries=[0.,*sorted(events),s.duration_s];segments=[];trips={}
        for left,right in zip(boundaries,boundaries[1:]):
            cursor=left
            while cursor<right:
                rebuild();indices=[];guards=[]
                for i,address in thermal.items():
                    if latched[i]:continue
                    def crossing(t,x,address=address,threshold=motors[i].thermal.trip_level):return x[address]-threshold
                    crossing.terminal=True;crossing.direction=1;indices.append(i);guards.append(crossing)
                sol=solve_ivp(rhs,(cursor,right),state,method='DOP853',rtol=2e-11,atol=1e-12,max_step=min(.0005,s.step_s/2),dense_output=True,events=guards or None)
                if not sol.success:raise ValueError('physical SI integration failed')
                stop=float(sol.t[-1]);segments.append((cursor,stop,sol.sol,connected.copy(),scale,load_scale.copy()));state=sol.y[:,-1]
                triggered={i for i,roots in zip(indices,sol.t_events or []) if len(roots)}
                if triggered:
                    currents=electrical(state)[0]
                    for i,address in thermal.items():
                        p=motors[i].thermal;epsilon=64*np.finfo(float).eps*max(abs(state[address]),p.trip_level,1e-300)
                        if not latched[i] and connected[i] and state[address]>=p.trip_level-epsilon and (abs(currents[i])/parameters[i]['ibase'])**2>state[address]:triggered.add(i)
                for i in triggered:connected[i]=False;latched[i]=True;trips[motors[i].id]=stop
                for e in events.get(right,[]) if stop==right else []:
                    if e.kind=='source_voltage':scale=e.value
                    else:
                        i=next(i for i,m in enumerate(motors) if m.id==e.equipment_id)
                        if e.kind=='load_torque_scale':load_scale[i]=e.value
                        elif e.kind=='motor_open':connected[i]=False
                        elif not latched[i]:connected[i]=True
                if stop==cursor and not triggered:raise ValueError('SI integration did not progress')
                cursor=stop
    alignment={}
    if hybrid is not None:
        boundaries=[t for t,side in zip(report['time_s'],report['sample_side']) if side=='pre_event']
        if boundaries:
            if len(boundaries)!=len(changes) or any(abs(a-b)>1e-6 for a,b in zip(boundaries,changes)):raise ValueError('independent group event clock mismatch')
            alignment=dict(zip(boundaries,changes))
    result={m.id:[] for m in motors};heat={m.id:[] for m in motors if m.thermal};control_channels={};generator_channels={}
    for time,side in zip(report['time_s'],report['sample_side']):
        at=(alignment.get(time,time) if hybrid is not None else next((t for t in trips.values() if abs(t-time)<1e-6),time)) if side in {'pre_event','post_event'} else time
        choices=[seg for seg in segments if seg[0]<=at<=seg[1]] if hybrid is not None else [seg for seg in segments if seg[0]-1e-13<=at<=seg[1]+1e-13]
        if not choices:raise ValueError('independent group sample outside integration domain')
        segment=choices[0] if side=='pre_event' else choices[-1]
        left,right,dense,connected,scale,load_scale,*control=segment;rebuild();x=dense(min(right,max(left,at)));stator,rotor,v,_,ig=electrical(x)
        for i,(m,p,flux) in enumerate(zip(motors,parameters,fluxes(x))):
            weights=p['lm']*np.linalg.solve(p['matrix'],np.ones(len(p['rr'])));contributions=1j*wb*weights*flux/p['vbase'];em=sum(contributions);power=3*v*np.conj(stator[i])
            row=[x[p['speed']],em.real,em.imag,abs(stator[i])/p['ibase'],abs(v)/p['vbase'],power.real,power.imag]
            if m.model=='double_cage':row += [contributions[0].real,contributions[0].imag,contributions[1].real,contributions[1].imag,abs(rotor[p['rotor']][0])/p['ibase'],abs(rotor[p['rotor']][1])/p['ibase'],3*p['rr'][0]*abs(rotor[p['rotor']][0])**2,3*p['rr'][1]*abs(rotor[p['rotor']][1])**2]
            result[m.id].append(row)
            if i in thermal:heat[m.id].append(float(x[thermal[i]]))
        if generator:
            values=generator.signals(x[generator_slice],ig,v);total=sum(stator)-ig;source_terminal=source_v*scale-complex(source.r_ohm,source.x_ohm)*total;source_power=3*source_terminal*np.conj(total)
            values.update({source.id+'.p_w':source_power.real,source.id+'.q_var':source_power.imag})
            for key,value in values.items():generator_channels.setdefault(key,[]).append(float(value))
        if hybrid is not None:
            sensed_values={i:sensed(x,i) for i,m in enumerate(motors) if m.contactor}
            for key,value in hybrid['signals'](control[0],at,connected,sensed_values).items():control_channels.setdefault(key,[]).append(value)
    return dict(channels={m:np.array(v) for m,v in result.items()},thermal=heat,trip_times_s=trips,**({'generator_channels':generator_channels} if generator else {}),**({'control_channels':control_channels,'independent_event_log':hybrid['event_log']} if hybrid is not None else {}))


def compare(spec,out=None):
    report=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not report.get('completed'):raise ValueError('production solve incomplete: '+str(report.get('status')))
    oracle=spec.model_copy(deep=True);control_identity=True;expected=[]
    if any(m.contactor for m in oracle.induction.motors):
        from power_grid_env.induction import InductionEvent
        if [(e.time_s,e.kind,e.value) for e in oracle.induction.events]!=[(.2,'source_voltage',.5),(.5,'source_voltage',1.)]:raise ValueError('controlled reference only covers authored .2/.5 plateau')
        for m in oracle.induction.motors:
            p=m.contactor
            if p is None or p.sense_node_id!='src' or not m.connected or p.maximum_automatic_restarts<1 or m.thermal:raise ValueError('controlled reference fixture outside authored scope')
            opened=.2+p.dropout_delay_s;closed=max(.5+p.pickup_delay_s,opened+p.minimum_off_time_s)
            expected.extend([dict(time_s=opened,kind='motor_open',equipment_id=m.id),dict(time_s=closed,kind='motor_close',equipment_id=m.id)]);m.contactor=None
        expected.sort(key=lambda e:e['time_s']);oracle.induction.events += [InductionEvent.model_validate(e) for e in expected];oracle.induction.events.sort(key=lambda e:e.time_s)
        got=[e for e in report['event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]
        control_identity=len(got)==len(expected) and all((a['kind'],a['equipment_id'])==(b['kind'],b['equipment_id']) and abs(a['time_s']-b['time_s'])<1e-8 for a,b in zip(got,expected))
    independent=reference(oracle,report);errors={};limits=LIMITS|dict.fromkeys(['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu'],2e-5)
    for m in spec.induction.motors:
        keys=KEYS+(['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','rotor1_copper_loss_w','rotor2_copper_loss_w'] if m.model=='double_cage' else [])
        errors[m.id]={key:float(max(abs(np.array(report['channels'][m.id+'.'+key])-independent['channels'][m.id][:,j]))) for j,key in enumerate(keys)}
        if m.thermal:errors[m.id]['thermal_level']=float(max(abs(np.array(report['channels'][m.id+'.thermal_level'])-independent['thermal'][m.id])))
    limits.update(rotor1_current_pu=1e-4,rotor2_current_pu=1e-4,rotor1_copper_loss_w=100.,rotor2_copper_loss_w=100.)
    limits['thermal_level']=2e-5
    production_trips=[e for e in report['event_log'] if e['kind']=='thermal_trip'];production_times={e['equipment_id']:e['time_s'] for e in production_trips}
    trip_identity_matches=len(production_trips)==len(independent['trip_times_s']) and set(production_times)==set(independent['trip_times_s'])
    trip_errors={m:abs(t-production_times[m]) for m,t in independent['trip_times_s'].items() if m in production_times}
    result=dict(control_identity_matches=control_identity,expected_control_actions=expected,trip_identity_matches=trip_identity_matches,passed=control_identity and trip_identity_matches and all(v<=limits[k] for row in errors.values() for k,v in row.items()) and all(v<=1e-6 for v in trip_errors.values()),maximum_errors=errors,limits=limits,trip_errors_s=trip_errors,code_sha256=package_fingerprint(),field_validated=False,industrial_profile_qualified=False)
    if out:
        write_json(Path(out)/'production.json',report);write_json(Path(out)/'si-reference.json',dict(time_s=report['time_s'],sample_side=report['sample_side'],channels={m:v.tolist() for m,v in independent['channels'].items()},thermal=independent['thermal'],trip_times_s=independent['trip_times_s']));write_json(Path(out)/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();rows=[]
    for name in ['cold','loaded_sag','coast','mixed_group','thermal','mutual_leakage','frequency_base','controlled_restart']:
        path=Path('cases/studies/double_cage_'+name+'.json');s=StudySpec.model_validate_json(path.read_text());r=compare(s,a.out/name);r.update(case=str(path),case_sha256=sha256_file(path));rows.append(r)
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,source_sha256=sha256_file(Path(__file__)),code_sha256=package_fingerprint(),field_validated=False,industrial_profile_qualified=False);write_json(a.out/'summary.json',result);print([(r['case'],r['passed'],r['maximum_errors']) for r in rows])
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()

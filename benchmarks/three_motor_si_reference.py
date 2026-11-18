"""Independent multi-bus SI rotor-flux circuit and DOP853 trajectories.

Only raw network/machine passports are consumed. No production stamps, motor
constants, RHS, integrator or model state transformation is called. Qualified
scope: balanced diagonal passive lines, constant-Z loads, one Thevenin source,
single-cage fan motors, stalled initial state and prescribed open/close/sag.
"""
import argparse
import math
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.integrate import solve_ivp

from power_grid_env.study_artifacts import read_json, encoded, digest, seal, verify
from power_grid_env.evidence import package_fingerprint

KEYS=('speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var','thermal_level')
LIMITS=dict(zip(KEYS,(2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.,2e-5)))


class Circuit:
    def __init__(self, study):
        network=study['network'];cfg=study['induction'];self.motors=cfg['motors']
        if cfg['operating_point'] is not None or len(network['sources'])!=1:
            raise ValueError('one-source stalled initialization reference only')
        if any(network[k] for k in ('transformers','generators','inverters','storages','shunts','switches','study_faults')):
            raise ValueError('unsupported independent circuit equipment')
        self.n=len(network['connectivity_nodes']);self.k=len(self.motors)
        self.nodes={v['id']:i for i,v in enumerate(network['connectivity_nodes'])}
        terminal={v['id']:self.nodes[v['connectivity_node_id']] for v in network['terminals']}
        self.wb=2*math.pi*network['frequency_hz']
        self.y=np.zeros((self.n,self.n),complex)
        src=network['sources'][0];self.source=terminal[src['terminal_id']]
        self.source_y=1/complex(src['r_ohm'],src['x_ohm'])
        self.y[self.source,self.source]+=self.source_y
        self.source_v=src['ll_v']/math.sqrt(3)*np.exp(1j*src['angle_a_rad'])
        voltage=network['voltage_levels'][0]['nominal_ll_v']/math.sqrt(3)
        for line in network['lines']:
            r,x,b=[np.asarray(line[k]) for k in ('r_ohm','x_ohm','b_siemens')]
            if not np.allclose(r,np.eye(3)*r[0,0],atol=0,rtol=0) or not np.allclose(x,np.eye(3)*x[0,0],atol=0,rtol=0) or np.any(b):
                raise ValueError('balanced diagonal noncapacitive lines required')
            a,c=terminal[line['terminal_from']],terminal[line['terminal_to']]
            admittance=1/complex(r[0,0],x[0,0])
            self.y[a,a]+=admittance;self.y[c,c]+=admittance
            self.y[a,c]-=admittance;self.y[c,a]-=admittance
        for load in network['loads']:
            if load['model']!='constant_z' or load['connection']!='wye':raise ValueError('constant-Z wye reference only')
            p,q=load['p_w'],load['q_var']
            if any(p[k]!=p['A'] or q[k]!=q['A'] for k in ('A','B','C')):raise ValueError('balanced loads required')
            self.y[terminal[load['terminal_id']],terminal[load['terminal_id']]]+=complex(p['A'],-q['A'])/voltage**2
        self.locations=[self.nodes[m['node_id']] for m in self.motors]
        self.params=[]
        for m in self.motors:
            if (m['model']!='single_cage' or m.get('winding_thermal') or m['contactor'] or m['mechanical_load']):
                raise ValueError('single-cage fan motors without winding R(T) or automatic contactors required')
            if m['thermal'] is not None and m['thermal']['model']!='stator_i2_replica':
                raise ValueError('only passive current-squared replica supported')
            base=m['rated_ll_v']**2/m['rated_va']
            self.params.append((m['stator_r_pu']*base,m['rotor_r_pu']*base,
                m['magnetizing_x_pu']*base/self.wb,
                (m['stator_x_pu']+m['magnetizing_x_pu'])*base/self.wb,
                (m['rotor_x_pu']+m['magnetizing_x_pu'])*base/self.wb))
        self.params=np.array(self.params)
        self.ratings=np.array([m['rated_va'] for m in self.motors])
        self.vbase=np.array([m['rated_ll_v']/math.sqrt(3) for m in self.motors])
        self.ibase=self.ratings/(3*self.vbase)
        self.connected=np.array([m['connected'] for m in self.motors])
        self.scale=1.
        self.rebuild()

    def rebuild(self):
        n,k=self.n,self.k
        matrix=np.zeros((n+2*k,n+2*k),complex)
        matrix[:n,:n]=self.y
        for i,node in enumerate(self.locations):
            rs,rr,lm,ls,lr=self.params[i]
            matrix[node,n+i]=1.
            if self.connected[i]:
                matrix[n+i,node]=1.
                matrix[n+i,n+i]=-rs-1j*self.wb*ls
                matrix[n+i,n+k+i]=-1j*self.wb*lm
            else:matrix[n+i,n+i]=1.
            matrix[n+k+i,n+i]=lm;matrix[n+k+i,n+k+i]=lr
        self.inverse=np.linalg.inv(matrix)

    def algebra(self, state):
        n,k=self.n,self.k
        rhs=np.zeros(n+2*k,complex)
        rhs[self.source]=self.source_y*self.source_v*self.scale
        rhs[n+k:]=state[:,0]+1j*state[:,1]
        answer=self.inverse@rhs
        voltage,current,rotor=answer[:n],answer[n:n+k],answer[n+k:]
        torque=-3*self.wb*self.params[:,2]*(rotor*np.conj(current)).imag/self.ratings
        return voltage,current,rotor,torque

    def rhs(self,t,flat):
        state=flat.reshape(self.k,4)
        _,current,rotor,torque=self.algebra(state)
        flux=state[:,0]+1j*state[:,1]
        dflux=-self.params[:,1]*rotor-1j*self.wb*(1-state[:,2])*flux
        load=np.array([m['fan_torque_pu']*w*abs(w) for m,w in zip(self.motors,state[:,2])])
        dw=(torque-load)/np.array([2*m['inertia_s'] for m in self.motors])
        heat=np.array([(abs(current[i])/self.ibase[i])**2-state[i,3] for i in range(self.k)])
        dh=np.array([heat[i]/m['thermal']['time_constant_s'] if m['thermal'] else 0. for i,m in enumerate(self.motors)])
        return np.column_stack((dflux.real,dflux.imag,dw,dh)).ravel()

    def observe(self,state):
        state=state.reshape(self.k,4)
        voltage,current,_,_=self.algebra(state)
        emf=1j*self.wb*self.params[:,2]/self.params[:,4]*(state[:,0]+1j*state[:,1])/self.vbase
        bus=voltage[self.locations]
        power=3*bus*np.conj(current)
        return np.column_stack((state[:,2],emf.real,emf.imag,abs(current)/self.ibase,
                                abs(bus)/self.vbase,power.real,power.imag,state[:,3]))

    def event(self,event):
        if event['kind']=='source_voltage':self.scale=event['value']
        elif event['kind'] in ('motor_open','motor_close'):
            i=next(i for i,m in enumerate(self.motors) if m['id']==event['equipment_id'])
            self.connected[i]=event['kind']=='motor_close'
        else:raise ValueError('unsupported independent event')
        self.rebuild()


def reference(study, axes):
    c=Circuit(study);cfg=study['induction'];events={}
    for event in cfg['events']:events.setdefault(event['time_s'],[]).append(event)
    boundaries=[0.,*sorted(events),cfg['duration_s']]
    state=np.zeros((c.k,4))
    state[:,3]=[m['thermal']['initial_level'] if m['thermal'] else 0. for m in c.motors]
    state=state.ravel()
    outputs=[np.full((len(times),c.k,len(KEYS)),np.nan) for times,sides in axes]
    for left,right in zip(boundaries,boundaries[1:]):
        solution=solve_ivp(c.rhs,(left,right),state,method='DOP853',rtol=2e-11,atol=1e-12,
                           max_step=.0005,dense_output=True)
        if not solution.success:raise ValueError('independent integration failed')
        for (times,sides),out in zip(axes,outputs):
            times=np.array(times,copy=True);sides=np.asarray(sides)
            for boundary in (left,right):
                tied=(abs(times-boundary)<=64*np.finfo(float).eps*max(1.,abs(boundary))) & np.isin(sides,['pre_event','post_event'])
                times[tied]=boundary
            selected=((times>left)&(times<right))|((times==left)&(sides!='pre_event'))|((times==right)&(sides!='post_event'))
            for i in np.flatnonzero(selected):
                out[i]=c.observe(solution.sol(times[i]))
                if any(m['thermal'] and out[i,j,-1]>=m['thermal']['trip_level'] for j,m in enumerate(c.motors)):
                    raise ValueError('automatic thermal trip outside prescribed-switch reference profile')
        state=solution.y[:,-1]
        for event in events.get(right,[]):c.event(event)
    if any(not np.isfinite(o).all() for o in outputs):raise ValueError('reference sample/side coverage incomplete')
    return outputs


def check_case(job):
    root,case=job
    parent=Path(root)/'studies'/case['id']
    reports=[read_json(parent/('grid-'+str(g))/'result.json') for g in range(3)]
    studies=[read_json(parent/('grid-'+str(g))/'study.json') for g in range(3)]
    fine=studies[-1]
    for study in studies:
        other=dict(study);other['induction']=dict(study['induction'],step_s=fine['induction']['step_s'])
        if other!=fine:raise ValueError('grid studies differ beyond output grid')
    predicted=reference(fine,[(np.asarray(r['time_s']),r['sample_side']) for r in reports])
    rows=[]
    for grid,(actual,values) in enumerate(zip(reports,predicted)):
        errors={}
        for i,motor in enumerate(fine['induction']['motors']):
            y=np.column_stack([actual['channels'][motor['id']+'.'+key] for key in KEYS])
            errors[motor['id']]=dict(zip(KEYS,np.max(abs(y-values[:,i,:]),axis=0).tolist()))
        passed=all(row[key]<=LIMITS[key] for row in errors.values() for key in KEYS)
        rows.append(dict(grid=grid,passed=passed,maximum_errors=errors))
    if not all(r['passed'] for r in rows):raise ValueError('independent multi-bus SI trajectory mismatch: '+str(rows))
    print('SI dynamic reference',Path(root).name,case['id'],'3 grids pass',flush=True)
    return dict(design=Path(root).name,case=case['id'],rows=rows)


def run(source,source_manifest,out):
    if verify(source)!=source_manifest:raise ValueError('owner physics source differs')
    summary=read_json(Path(source)/'summary.json');jobs=[]
    for row in summary['rows']:
        folder=Path(source)/row['id']
        for case in read_json(folder/'plan.json')['cases']:jobs.append((str(folder),case))
    with ProcessPoolExecutor(max_workers=2) as pool:rows=list(pool.map(check_case,jobs))
    result=dict(passed=True,code_sha256=package_fingerprint(),reference_sha256=digest(Path(__file__).read_bytes()),
        source_manifest_sha256=source_manifest,reference_trajectories=len(rows),grid_comparisons=len(rows)*3,
        limits=LIMITS,rows=rows,initialization='zero rotor flux and speed; passport current-squared replica level',
        method='Independent SI nodal/stator/rotor circuit and DOP853',
        scope='Authored balanced radial 3-node3-motor fan cases with passive thermal replicas only; no automatic-trip, winding-thermal, full-family, saturation or OEM qualification.')
    if verify(source)!=source_manifest:raise ValueError('source changed')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'receipt.json').write_bytes(encoded(result));seal(root)
    return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('source','source-manifest','out'):p.add_argument('--'+k,required=True)
    print(run(**vars(p.parse_args())))

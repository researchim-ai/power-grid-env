"""Independent dense phase MNA versus OpenDSS for finite-impedance faults.

Common-point LLG uses two phase legs and one shared ground impedance, distinct
from the existing pair of independent phase-to-ground faults. Static phasor
verification only; no negative-sequence rotor dynamics is implied.
"""
import itertools,math
import numpy as np
from power_grid_env.build import NetBuilder
from power_grid_env.model import StudyFault
from power_grid_env.backend_opendss import solve_network

CASES=[(mesh,kind,phases) for mesh in [False,True] for kind,choices in [
    ('slg',[['A'],['B'],['C']]),('ll',[['A','B'],['B','C'],['C','A']]),
    ('llg',[['A','B'],['B','C'],['C','A']]),('llg_common',[['A','B'],['B','C'],['C','A']]),('3ph',[['A','B','C']])] for phases in choices]


def example(mesh,kind,phases):
    b=NetBuilder('phase-fault','Authored multi-node asymmetric finite-fault corpus',10.,s_base_va=1e6)
    for node in ['grid','a','b']:b.node(node)
    b.source('grid',2.,8.);b.line_z1('grid','a',.3+.8j,line_id='l1');b.line_z1('a','b',.4+1.1j,line_id='l2')
    if mesh:b.line_z1('grid','b',.5+1.5j,line_id='tie')
    b.load('b',30000.,10000.,model='constant_z',load_id='load');net=b.build();net.sources[0].r0_ohm=5.;net.sources[0].x0_ohm=15.
    for line in net.lines:
        for i in range(3):
            for j in range(3):
                if i!=j:line.r_ohm[i][j]=.04;line.x_ohm[i][j]=.12
    net.loads[0].p_w=dict(A=7000.,B=10000.,C=13000.);net.loads[0].q_var=dict(A=2000.,B=3000.,C=5000.)
    kw=dict(phase_leg_r_ohm=.03,phase_leg_x_ohm=.02) if kind=='llg_common' else {}
    net.study_faults=[StudyFault(id='fault',bus_id='b',kind=kind,phases=phases,r_ohm=.4,x_ohm=.2,**kw)]
    return net


def reference(net):
    nodes=sorted(n.id for n in net.connectivity_nodes);index={n:i for i,n in enumerate(nodes)};n=3*len(nodes)
    terminals={t.id:t.connectivity_node_id for t in net.terminals};Y=np.zeros((n,n),complex);rhs=np.zeros(n,complex)
    def row(node):return np.arange(3*index[node],3*index[node]+3)
    for line in net.lines:
        y=np.linalg.inv(np.array(line.r_ohm)+1j*np.array(line.x_ohm));a=row(terminals[line.terminal_from]);b=row(terminals[line.terminal_to])
        Y[np.ix_(a,a)]+=y;Y[np.ix_(b,b)]+=y;Y[np.ix_(a,b)]-=y;Y[np.ix_(b,a)]-=y
    for s in net.sources:
        z1=complex(s.r_ohm,s.x_ohm);z0=complex(s.r0_ohm,s.x0_ohm);z=np.eye(3)*z1+np.ones((3,3))*(z0-z1)/3;y=np.linalg.inv(z)
        a=row(terminals[s.terminal_id]);emf=s.ll_v/np.sqrt(3)*np.exp(1j*(s.angle_a_rad+np.array([0,-2*np.pi/3,2*np.pi/3])))
        Y[np.ix_(a,a)]+=y;rhs[a]+=y@emf
    for load in net.loads:
        a=row(terminals[load.terminal_id]);S=np.array([complex(load.p_w[k],load.q_var[k]) for k in 'ABC']);Y[a,a]+=S.conj()/(10000**2/3)
    for fault in net.study_faults:
        p=np.array(['ABC'.index(x) for x in fault.phases]);a=row(fault.bus_id)[p];yg=1/complex(fault.r_ohm,fault.x_ohm)
        if fault.kind=='ll':
            d=np.zeros(n);d[a[0]]=1;d[a[1]]=-1;Y+=yg*np.outer(d,d)
        elif fault.kind=='llg_common':
            leg=1/complex(fault.phase_leg_r_ohm,fault.phase_leg_x_ohm)
            Y[np.ix_(a,a)]+=np.eye(2)*leg-np.ones((2,2))*leg**2/(yg+2*leg)
        else:Y[a,a]+=yg
    v=np.linalg.solve(Y,rhs);flows={}
    for line in net.lines:
        a=row(terminals[line.terminal_from]);b=row(terminals[line.terminal_to]);flows[line.id]=np.linalg.solve(np.array(line.r_ohm)+1j*np.array(line.x_ohm),v[a]-v[b])
    fault_currents={}
    for f in net.study_faults:
        if f.kind=='llg_common':
            phase_voltage=v[row(f.bus_id)[['ABC'.index(p) for p in f.phases]]]
            leg=1/complex(f.phase_leg_r_ohm,f.phase_leg_x_ohm);ground=1/complex(f.r_ohm,f.x_ohm)
            point=leg*sum(phase_voltage)/(ground+2*leg)
            fault_currents.update({f.id+':leg:'+p:leg*(value-point) for p,value in zip(f.phases,phase_voltage)})
            fault_currents[f.id+':ground']=ground*point
    return dict(nodes=nodes,voltage=v.reshape(-1,3),flows=flows,fault_currents=fault_currents,kcl=float(max(abs(Y@v-rhs))))


def check(net,*,ref=None,primary=None):
    ref=reference(net) if ref is None else ref;primary=solve_network(net) if primary is None else primary
    if primary.status!='converged':return dict(passed=False,status=primary.status,diagnostics=primary.diagnostics)
    from power_grid_env.topology import bus_identifier
    errors=[];current_errors=[]
    for i,node in enumerate(ref['nodes']):
        for j,phase in enumerate('ABC'):
            value=next(x.complex for x in primary.voltages if x.bus_id==bus_identifier([node]) and x.phase==phase)
            errors.append(abs(value-ref['voltage'][i,j])/max(1.,abs(ref['voltage'][i,j])))
    for id_,current in ref['flows'].items():
        for j,phase in enumerate('ABC'):
            value=next(x.current for x in primary.flows if x.equipment_id==id_ and x.terminal_index==0 and x.phase==phase)
            current_errors.append(abs(value-current[j])/max(1.,abs(current[j])))
    for id_,current in ref['fault_currents'].items():
        value=next(x.current for x in primary.flows if x.equipment_id==id_ and x.terminal_index==0)
        current_errors.append(abs(value-current)/max(1.,abs(current)))
    return dict(passed=bool(max(errors)<2e-5 and max(current_errors)<2e-5 and ref['kcl']<1e-8),maximum_voltage_relative=float(max(errors)),maximum_current_relative=float(max(current_errors)),independent_kcl_a=ref['kcl'])


def run(out):
    from pathlib import Path
    from power_grid_env.report import write_json,sha256_file
    from power_grid_env.evidence import package_fingerprint
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh phase-fault output required')
    code=package_fingerprint();producer=sha256_file(Path(__file__));rows=[]
    for i,c in enumerate(CASES):
        net=example(*c);write_json(out/str(i)/'network.json',net.model_dump(mode='json'));ref=reference(net);primary=solve_network(net)
        write_json(out/str(i)/'reference.json',dict(nodes=ref['nodes'],voltage_real_v=ref['voltage'].real.tolist(),voltage_imag_v=ref['voltage'].imag.tolist(),
            current_real_a={k:v.real.tolist() for k,v in ref['flows'].items()},current_imag_a={k:v.imag.tolist() for k,v in ref['flows'].items()},fault_current_real_a={k:float(v.real) for k,v in ref['fault_currents'].items()},fault_current_imag_a={k:float(v.imag) for k,v in ref['fault_currents'].items()},kcl_a=ref['kcl']))
        write_json(out/str(i)/'primary.json',primary.model_dump(mode='json'));row=check(net,ref=ref,primary=primary);write_json(out/str(i)/'comparison.json',row);rows.append(row)
    if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)):raise ValueError('phase-fault producer changed')
    result=dict(passed=all(r['passed'] for r in rows),cases=len(rows),code_sha256=code,producer_sha256=producer,static_phasor_only=True,
        maximum_current_relative=max(r['maximum_current_relative'] for r in rows),maximum_voltage_relative=max(r['maximum_voltage_relative'] for r in rows),
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*.json') if p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);a=parser.parse_args();r=run(a.out);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)

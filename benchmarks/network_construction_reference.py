"""Independent catalog-to-SI nodal screening and OpenDSS passive fault currents."""
import math
import importlib.metadata
import numpy as np


def reference(task,proposal,compilation,plan):
    catalog=task['catalog'];a=catalog['assumptions'];voltage=task['mandatory_cases'][0]['study']['network']['voltage_levels'][0]['nominal_ll_v']
    trans=next(t for t in catalog['transformers'] if t['id']==proposal['transformer_id'])
    rating=trans['rating_kva']['value']*1000
    resistance=voltage**2*trans['load_loss_w']['value']/rating**2
    magnitude=trans['impedance_percent']['value']/100*voltage**2/rating
    zt=complex(resistance,math.sqrt(magnitude*magnitude-resistance*resistance))
    routes={r['id']:r for r in task['corridors']};cables={c['id']:c for c in catalog['cables']}
    nodes=sorted(task['node_demand_kva']);index={n:i for i,n in enumerate(nodes)}
    original=task['mandatory_cases'][0]['study']['network'];terminals={t['id']:t['connectivity_node_id'] for t in original['terminals']}
    root=terminals[original['sources'][0]['terminal_id']];source=index[root];vph=voltage/math.sqrt(3)
    y=np.zeros((len(nodes),len(nodes)),complex);y[source,source]+=1/zt
    rhs=np.zeros(len(nodes),complex);rhs[source]=vph/zt
    pf=task['lagging_power_factor'];p=complex(pf,math.sqrt(1-pf*pf));branches=[]
    for node,kva in task['node_demand_kva'].items():rhs[index[node]]-=np.conj(kva*1000*p/(3*vph))
    for selection in proposal['branches']:
        route=routes[selection['corridor_id']];cable=cables[selection['cable_id']]
        hot=cable['dc_r20_ohm_km']['value']*(1+a['copper_alpha_per_c']['value']*(a['conductor_temperature_c']['value']-20))*a['ac_dc_resistance_factor']['value']
        z=complex(hot,a['cable_reactance_ohm_km']['value'])*route['installation']['length_m']/1000
        i,j=[index[route[k]] for k in ('node_a','node_b')]
        y[i,i]+=1/z;y[j,j]+=1/z;y[i,j]-=1/z;y[j,i]-=1/z
        branches.append(dict(id=route['id'],i=i,j=j,z=z))
    volts=np.linalg.solve(y,rhs);static=compilation['static'];checks=[]
    def check(name,value,target,limit):
        error=abs(value-target);checks.append(dict(id=name,error=float(error),limit=limit,passed=bool(error<=limit)))
    for row in static['nodes']:
        v=volts[index[row['node_id']]]
        check(row['node_id']+'.V',v,complex(row['voltage_real_v'],row['voltage_imag_v']),1e-8)
    for row in static['branches']:
        b=next(b for b in branches if b['id']==row['corridor_id'])
        check(row['corridor_id']+'.I',abs((volts[b['i']]-volts[b['j']])/b['z']),row['nominal_current_a'],1e-8)
    for case in plan['cases']:
        network=case['study']['network'];t={t['id']:t['connectivity_node_id'] for t in network['terminals']}
        check(case['id']+'.sourceZ',complex(network['sources'][0]['r_ohm'],network['sources'][0]['x_ohm']),zt,1e-12)
        for line in network['lines']:
            b=next(b for b in branches if b['id']==line['id'])
            if {t[line['terminal_from']],t[line['terminal_to']]}!={nodes[b['i']],nodes[b['j']]}:raise ValueError('compiled route endpoints differ')
            for key,target in [('r_ohm',b['z'].real),('x_ohm',b['z'].imag)]:
                for i in range(3):
                    for j in range(3):check(case['id']+'.'+line['id']+'.'+key+str(i)+str(j),line[key][i][j],target if i==j else 0.,1e-12)
    import opendssdirect
    d=opendssdirect.NewContext();faults=[]
    for node in nodes:
        d.Basic.ClearAll()
        commands=['set defaultbasefrequency=50',f'new circuit.ref basekv={voltage/1000:.17g} pu=1 phases=3 bus1=n{source} basefreq=50 r1={zt.real:.17g} x1={zt.imag:.17g} r0={zt.real:.17g} x0={zt.imag:.17g}']
        for k,b in enumerate(branches):commands.append(f'new line.l{k} bus1=n{b["i"]} bus2=n{b["j"]} phases=3 length=1 units=km r1={b["z"].real:.17g} x1={b["z"].imag:.17g} r0={b["z"].real:.17g} x0={b["z"].imag:.17g} c1=0 c0=0')
        commands += [f'new fault.test phases=3 bus1=n{index[node]} bus2=n{index[node]}.0.0.0 r=1e-8','set controlmode=off','solve mode=snapshot frequency=50']
        for command in commands:d.Text.Command(command)
        if not d.Solution.Converged():raise ValueError('independent OpenDSS fault did not converge')
        d.Circuit.SetActiveElement('fault.test');currents=d.CktElement.CurrentsMagAng()[::2][:3]
        target=next(r['source_fault_a'] for r in static['nodes'] if r['node_id']==node)
        if len(currents)!=3 or not all(math.isfinite(v) and v>0 for v in currents):raise ValueError('invalid reference currents')
        errors=[abs(v-target)/target for v in currents]
        checks.append(dict(id=node+'.OpenDSS_fault',relative_error=max(errors),limit=2e-6,passed=max(errors)<=2e-6))
        faults.append(dict(node_id=node,currents_a=currents,commands=commands))
    return dict(passed=all(c['passed'] for c in checks),checks=checks,fault_solutions=faults,
                engine=d.Basic.Version(),wrapper=importlib.metadata.version('opendssdirect.py'),
                scope='Independent nominal-current SI nodal equations and OpenDSS passive three-phase faults; no external dynamic motor validation or manufacturer authentication')

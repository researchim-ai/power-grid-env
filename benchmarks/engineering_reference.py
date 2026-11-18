"""Independent balanced constant-Z nodal audit; no production solver imports."""
import numpy as np
import math


def radial_reference(project):
    if project['profile']!='passive_power_flow' or any(project[k] for k in ('motors','generators','transformers','switches')):
        raise ValueError('reference supports passive lines/constant-Z only')
    if any(load.get('model','constant_z')!='constant_z' for load in project['loads']):
        raise ValueError('constant-Z reference only')
    nodes=[n['id'] for n in project['nodes']];root=project['source_node']
    adjacency={n:set() for n in nodes}
    for line in project['lines']:
        adjacency[line['from_node']].add(line['to_node']);adjacency[line['to_node']].add(line['from_node'])
    live={root};pending=[root]
    while pending:
        for other in adjacency[pending.pop()]-live:live.add(other);pending.append(other)
    ordered=[n for n in nodes if n in live];ix={n:i for i,n in enumerate(ordered)}
    y=np.zeros((len(ix),len(ix)),dtype=complex)
    for line in project['lines']:
        a,b=line['from_node'],line['to_node']
        if a not in ix or b not in ix:continue
        admittance=1/complex(line['r_ohm_km'],line['x_ohm_km'])*1000/line['length_m']
        i,j=ix[a],ix[b];y[i,i]+=admittance;y[j,j]+=admittance;y[i,j]-=admittance;y[j,i]-=admittance
    for load in project['loads']:
        if load['node_id'] in ix:
            y[ix[load['node_id']],ix[load['node_id']]]+=complex(load['p_kw'],-load['q_kvar'])*1000/project['nominal_ll_v']**2
    zs=complex(project['source_r_ohm'],project['source_x_ohm']);vs=project['nominal_ll_v']/np.sqrt(3)
    y[ix[root],ix[root]]+=1/zs;rhs=np.zeros(len(ix),complex);rhs[ix[root]]=vs/zs
    solution=np.linalg.solve(y,rhs);v={n:solution[ix[n]] if n in ix else 0j for n in nodes}
    currents={line['id']:float(abs((v[line['from_node']]-v[line['to_node']])/(complex(line['r_ohm_km'],line['x_ohm_km'])*line['length_m']/1000)))
        if line['from_node'] in live else None for line in project['lines']}
    source=3*v[root]*np.conj((vs-v[root])/zs)/1000 # Delivered bus-terminal power, excludes source internal impedance loss.
    return dict(voltage_pu={n:float(abs(value)/vs) for n,value in v.items()},energized={n:n in live for n in nodes},
        currents_a=currents,source_p_kw=float(source.real),source_q_kvar=float(source.imag))


def compare_reference(project,result):
    reference=radial_reference(project)
    if result.get('converged') is not True:raise ValueError('unfinished native study')
    def finite(value):
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
            raise ValueError('finite numeric measurement required')
        return value
    if len(result['nodes'])!=len(reference['voltage_pu']) or {n['id'] for n in result['nodes']}!=set(reference['voltage_pu']):
        raise ValueError('complete node measurement inventory required')
    if len(result['branches'])!=len(project['lines']) or {b['id'] for b in result['branches']}!={l['id'] for l in project['lines']}:
        raise ValueError('complete branch measurement inventory required')
    voltage_error=0.;current_error=0.;loading_error=0.

    for node in result['nodes']:
        if node['energized']!=reference['energized'][node['id']]:raise ValueError('energization disagrees with reference')
        if set(node['phases'])!={'A','B','C'}:raise ValueError('all phase measurements required')
        for phase in node['phases'].values():
            expected=reference['voltage_pu'][node['id']]
            value=finite(phase['voltage_pu']) if phase['energized'] else 0.
            voltage_error=max(voltage_error,abs(value-expected))
    for branch in result['branches']:
        expected=reference['currents_a'][branch['id']]
        actual=branch['from_terminal']
        if expected is None:
            if actual is not None:raise ValueError('dead branch must have no measurement')
        else:
            current_error=max(current_error,abs(finite(actual['current_a'])-expected))
            rating=next(l['rating_a'] for l in project['lines'] if l['id']==branch['id'])
            loading_error=max(loading_error,abs(finite(branch['loading_pct'])-expected/rating*100))
    source_error=max(abs(finite(result['source']['p_kw'])-reference['source_p_kw']),abs(finite(result['source']['q_kvar'])-reference['source_q_kvar']))
    if voltage_error>2e-6 or current_error>2e-4 or source_error>2e-4 or loading_error>2e-4:
        raise ValueError('independent nodal reference residual exceeds declared tolerance')
    return dict(maximum_voltage_error_pu=voltage_error,maximum_current_error_a=current_error,maximum_source_error_kva=source_error,maximum_loading_error_pct=loading_error,
        scope='Independent linear constant-Z nodal equations,not OEM/field validation')

"""Independent branch elimination reference for arbitrary frozen motor EMFs."""
import copy
import itertools
import math
from pathlib import Path

from power_grid_env.motor_fault_contribution import calculate
from power_grid_env.study_artifacts import read_json,encoded,seal,digest


def reference(s):
    zl=complex(s['line_r_ohm'],s['line_x_ohm']);p=s['fault_position_pu']
    zs=complex(s['source_r_ohm'],s['source_x_ohm'])+p*zl
    es=complex(s['source_emf_v']['real'],s['source_emf_v']['imag'])
    ys=1/zs if s.get('source_connected',True) else 0j
    load=complex(s['downstream_load_p_w'],-s['downstream_load_q_var'])/s['nominal_ll_v']**2
    ym=0j;jm=0j;motors=[]
    for m in s['motors']:
        xp=m['stator_x_pu']+m['rotor_x_pu']*m['magnetizing_x_pu']/(m['rotor_x_pu']+m['magnetizing_x_pu'])
        z=complex(m['stator_r_pu'],xp)*m['rated_ll_v']**2/m['rated_va']
        emf=complex(m['emf_pu']['real'],m['emf_pu']['imag'])*m['rated_ll_v']/math.sqrt(3)
        y=1/z if m['connected'] else 0j
        ym+=y;jm+=emf*y;motors.append((m['id'],y,emf))
    yd=ym+load
    # Eliminate the downstream bus analytically, retaining passive load.
    path=(1-p)*zl
    effective_y=yd/(1+path*yd)
    effective_j=jm/(1+path*yd)
    zf=complex(s['fault_r_ohm'],s['fault_x_ohm'])
    vf=(ys*es+effective_j)/(ys+effective_y+1/zf) if zf else 0j
    vd=(vf+path*jm)/(1+path*yd)
    source=ys*(es-vf)
    motorcurrents={name:y*(emf-vd) for name,y,emf in motors}
    fault=source+sum(motorcurrents.values())-load*vd
    return dict(fault_a=fault,source_a=source,**{'motor:'+k+'_a':v for k,v in motorcurrents.items()})


def qualify(snapshot,out):
    rows=[]
    for position,rf,phase,voltage,connected,count in itertools.product([0,.5,1],[0,.001,.1],[-2,0,2],[.95,1.05],[False,True],[1,3]):
        s=copy.deepcopy(snapshot);s.update(fault_position_pu=position,fault_r_ohm=rf,source_connected=connected)
        s['source_emf_v']['real']*=voltage
        original=s['motors'][0];s['motors']=[]
        for i in range(count):
            m=copy.deepcopy(original);m['id']='m'+str(i)
            m['emf_pu']=dict(real=.95*math.cos(phase+.2*i),imag=.95*math.sin(phase+.2*i))
            s['motors'].append(m)
        actual=calculate(s);expected=reference(s)
        errors={k:abs(complex(actual['currents'][k]['real'],actual['currents'][k]['imag'])-v) for k,v in expected.items()}
        rows.append(dict(position=position,fault_r_ohm=rf,phase=phase,source_connected=connected,motor_count=count,voltage_factor=voltage,
                         errors_a=errors,passed=max(errors.values())<=2e-7 and actual['passed_algebra_audits']))
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    result=dict(passed=all(r['passed'] for r in rows),cases=len(rows),
                maximum_error_a=max(max(r['errors_a'].values()) for r in rows),
                producer_sha256=digest(Path(__file__).read_bytes()),
                scope='Independent scalar branch elimination; frozen EMF algebra only, not independent trajectories or real motor data.')
    for name,value in [('input',snapshot),('rows',rows),('summary',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(read_json(a.input),a.out);print(r)
    if not r['passed']:raise SystemExit(2)

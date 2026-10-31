"""Explicit open-stator boundary adaptation of native Motor5, not stock u=0.

Only two algebraic equations change. All native rotor/mechanical differential
laws, time constants, services and other algebraic equations remain identical.
Generated code lives in a disposable private cache, never the stock cache.
"""
from pathlib import Path
import hashlib,json,math
import numpy as np
from scipy.optimize import brentq
from power_grid_env.report import sha256_file


def equations(model):
    return {k:dict(e_str=v.e_str,t_const=getattr(getattr(v,'t_const',None),'name',None)) for k,v in model.states.items()}


def install_terminal_gate(system):
    from andes.core import NumParam
    model=system.Motor5;before=equations(model)
    original={k:v.e_str for k,v in model.algebs.items()}
    services={k:v.v_str for k,v in model.services.items()}
    model.closed=NumParam(default=1.,vrange=(0,1),info='Explicit benchmark stator connection, rotor remains active')
    for key in ['Id','Iq']:
        variable=getattr(model,key);variable.e_str=f'closed * ({variable.e_str}) + (1-closed) * {key}'
    if equations(model)!=before or {k:v.v_str for k,v in model.services.items()}!=services:raise ValueError('native differential laws/services changed')
    if any(v.e_str!=original[k] for k,v in model.algebs.items() if k not in {'Id','Iq'}):raise ValueError('nonterminal native algebraic law changed')
    model.cache.refresh()
    system.prepare(quick=True,models=['Motor5'],nomp=True)
    if equations(model)!=before:raise ValueError('codegen changed native differential laws')
    cache=Path(system.options['pycode_path'])
    return dict(kind='adapted_native_Motor5_two_algebraic_stator_constraints',stock_black_box_coast_claim=False,
        differential_equations_unchanged=True,native_differential_equations=before,
        original_terminal_equations={k:original[k] for k in ['Id','Iq']},
        adapted_terminal_equations={k:getattr(model,k).e_str for k in ['Id','Iq']},
        generated_Motor5_sha256=sha256_file(cache/'Motor5.py'),
        generated_source_sha256={p.name:sha256_file(p) for p in sorted(cache.glob('*.py'))},
        dynamic_equations_sha256=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest())


def initial_motor(m,frequency,operating_point):
    """Native circuit-coordinate equilibrium; no production states or constants."""
    if operating_point is None:return dict(speed_pu=0.,e1=0j,e2=0j,admittance=1/complex(m.stator_r_pu,m.stator_x_pu+m.rotor_x_pu*m.rotor2_x_pu*m.magnetizing_x_pu/(m.rotor_x_pu*m.rotor2_x_pu+m.rotor_x_pu*m.magnetizing_x_pu+m.rotor2_x_pu*m.magnetizing_x_pu)))
    wb=2*math.pi*frequency;xm=m.magnetizing_x_pu;xs=m.stator_x_pu
    x0=xs+xm;x1=xs+m.rotor_x_pu*xm/(m.rotor_x_pu+xm)
    x2=xs+m.rotor_x_pu*m.rotor2_x_pu*xm/(m.rotor_x_pu*m.rotor2_x_pu+m.rotor_x_pu*xm+m.rotor2_x_pu*xm)
    rate1=wb*m.rotor_r_pu/(m.rotor_x_pu+xm)
    rate2=wb*m.rotor2_r_pu/(m.rotor2_x_pu+m.rotor_x_pu*xm/(m.rotor_x_pu+xm))
    matrix=np.array([[-rate1,0.],[rate2-rate1,-rate2]])
    drive=1j*np.array([(x0-x1)*rate1,(x0-x1)*rate1+(x1-x2)*rate2])
    def response(speed):
        emf=np.linalg.solve(1j*wb*(1-speed)*np.eye(2)-matrix,drive)
        current=1/(complex(m.stator_r_pu,x2)+emf[1])
        return emf*current,current
    # Voltage is supplied separately by the plant PV target, no trajectory fit.
    def point(voltage):
        def torque_error(speed):
            emf,current=response(speed);return float((emf[1]*np.conj(current)).real*voltage**2-m.fan_torque_pu*speed**2)
        grid=np.linspace(operating_point.minimum_speed_pu,1.,501);roots=[]
        for lo,hi in zip(grid,grid[1:]):
            if torque_error(lo)*torque_error(hi)<=0:roots.append(brentq(torque_error,lo,hi,xtol=1e-13))
        if not roots:raise ValueError('no independent native high-speed equilibrium')
        speed=max(roots);emf,current=response(speed)
        return dict(speed_pu=float(speed),e1=emf[0],e2=emf[1],admittance=current)
    return point

"""Past-only public electrical indications with explicit display quantization."""
import math
import numpy as np
from pydantic import Field,StrictBool
from typing import Literal
from power_grid_env.electrical_sizing import Contract
from power_grid_env.industrial_dol import IndustrialDOLCase


class IndicationDecision(Contract):
    motors:dict[str,Literal['disconnected','at_running_speed','below_running_speed']]=Field(default_factory=dict,max_length=8)
    insufficient_observation:StrictBool=False


class MotorIndication(Contract):
    motor_id:str=Field(min_length=1,max_length=128)
    node_id:str=Field(min_length=1,max_length=128)
    connected:StrictBool
    speed_indication_pu:float
    current_indication_pu:float=Field(ge=0)
    voltage_indication_pu:float=Field(ge=0)


def indication_rule():
    return dict(running_speed_pu=.9,speed_display_resolution_pu=.001,
                classification='disconnected first; otherwise compare the displayed speed indication with running_speed_pu')


def diagnosis(frame,mode='image'):
    if mode=='schematic':return IndicationDecision(insufficient_observation=True)
    if mode not in ('image','text','combined'):raise ValueError('unknown observation mode')
    return IndicationDecision(motors={m['motor_id']:('disconnected' if not m['connected'] else
        'at_running_speed' if m['speed_indication_pu']>=frame['rule']['running_speed_pu'] else 'below_running_speed') for m in frame['motors']})


def public_frame(case,trace,cutoff_s,side='post_event'):
    case=IndustrialDOLCase.model_validate(case)
    if not math.isfinite(cutoff_s) or cutoff_s<0 or side not in ('pre_event','post_event'):raise ValueError('invalid observation cutoff/side')
    if not isinstance(trace,dict) or trace.get('completed') is not True:raise ValueError('complete physical trace required')
    times=np.asarray(trace.get('time_s',[]),float);sides=trace.get('sample_side',[]);channels=trace.get('channels',{})
    if times.ndim!=1 or len(times)<2 or len(sides)!=len(times) or not np.isfinite(times).all() or np.any(np.diff(times)<0):raise ValueError('invalid observation axis')
    # Only roundoff-equal saved samples are eligible. No interpolation or
    # extrapolation introduces information from the other side of a switch.
    eps=64*np.finfo(float).eps*max(1.,abs(cutoff_s))
    found=np.flatnonzero(abs(times-cutoff_s)<=eps)
    if not len(found):raise ValueError('cutoff must match a saved sample')
    exact=[i for i in found if sides[i]==side]
    if exact:index=exact[0]
    elif len(found)==1 and sides[found[0]] in ('initial','regular'):index=int(found[0])
    else:raise ValueError('ambiguous observation boundary')
    keep=slice(0,index+1);values={};motors=[]
    for m in sorted(case.study.induction.motors,key=lambda m:m.id):
        for key in ('bus_voltage_pu','current_pu','speed_pu','connected'):
            name=m.id+'.'+key;array=np.asarray(channels.get(name,[]),float)
            if array.shape!=times.shape or not np.isfinite(array[keep]).all():raise ValueError('invalid observed channel')
            if key=='connected' and not np.isin(array[keep],[0.,1.]).all():raise ValueError('invalid observed connection')
            if key in ('bus_voltage_pu','current_pu') and np.any(array[keep]<0):raise ValueError('negative RMS indication')
            values[name]=array[keep].tolist()
        motors.append(dict(motor_id=m.id,node_id=m.node_id,connected=bool(values[m.id+'.connected'][-1]),
            speed_indication_pu=round(values[m.id+'.speed_pu'][-1],3),
            current_indication_pu=round(values[m.id+'.current_pu'][-1],3),
            voltage_indication_pu=round(values[m.id+'.bus_voltage_pu'][-1],3)))
    return dict(schema='power-grid-env.design-frame/v1',cutoff_s=float(times[index]),side=sides[index],
        rule=indication_rule(),
        motors=motors,history=dict(time_s=times[keep].tolist(),sample_side=sides[:index+1],channels=values),
        scope='Saved observations through the requested event side only; displayed speed classification, not feasibility or switching permission')


def extrema_indices(times,values,sides,buckets=400):
    """Keep endpoints, every event side and each bucket's actual min/max."""
    t=np.asarray(times,float);y=np.asarray(values,float)
    if t.ndim!=1 or y.shape!=t.shape or len(sides)!=len(t) or not len(t) or not np.isfinite(t).all() or not np.isfinite(y).all() or np.any(np.diff(t)<0) or buckets<1:raise ValueError('invalid waveform envelope input')
    kept={0,len(t)-1};width=max(1,math.ceil(len(t)/buckets))
    kept.update(i for i,s in enumerate(sides) if s in ('pre_event','post_event','initial'))
    for left in range(0,len(t),width):
        part=y[left:left+width];kept.update((left+int(np.argmin(part)),left+int(np.argmax(part))))
    return sorted(kept)

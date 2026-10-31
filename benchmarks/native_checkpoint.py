"""Scoped rollback of native DAE trials, including services and limiter memory.

The native checkpoint_state stores x/y/f only. Root trials additionally change
limiter flags, anti-windup x_set lists and cached services. Arrays are restored
in place, preserving cached native input bindings. No untrusted pickle is used.
"""
from __future__ import annotations
import copy
import hashlib
import numbers
import numpy as np


_SKIP=object()


def _clone(value):
    if isinstance(value,np.ndarray):
        return value.copy() if value.dtype.kind!='O' else _SKIP
    if value is None or isinstance(value,(str,bool,numbers.Number,np.generic)):
        return copy.deepcopy(value)
    if isinstance(value,(list,tuple)):
        items=[_clone(x) for x in value]
        return _SKIP if any(x is _SKIP for x in items) else type(value)(items)
    if isinstance(value,dict):
        items={k:_clone(v) for k,v in value.items()}
        return _SKIP if any(v is _SKIP for v in items.values()) else items
    return _SKIP


def _hash(value):
    if isinstance(value,np.ndarray):return b'A'+str((value.dtype.str,value.shape)).encode()+value.tobytes()
    if isinstance(value,(list,tuple)):return b'L'+b''.join(_hash(x) for x in value)
    if isinstance(value,dict):return b'D'+b''.join(str(k).encode()+_hash(v) for k,v in sorted(value.items()))
    return repr(value).encode()


class NativeCheckpoint:
    """Only the audited GENROU/TGOV1/SEXS and Motor3/Motor5 circuit is supported."""
    MODELS={'Bus','Slack','PV','Line','Shunt','GENROU','TGOV1','SEXS','Motor3','Motor5'}
    DISCRETE={'LessThan','IsEqual','Limiter','AntiWindup','Switcher','HardLimiter','SortedLimiter'}
    TDS_FIELDS=('h','custom_event','last_converged','converged','busted','chatter','err_msg','niter','_last_switch_t','mis','mis_inc')

    def __init__(self,system):
        if system.TDS.config.honest!=1 or type(system.TDS.method).__name__!='Trapezoid':
            raise ValueError('native rollback requires audited honest trapezoidal stepping')
        self.system=system;self.items=[];self.discrete_inventory={}
        def take(obj,key,label):
            if not hasattr(obj,key):return
            original=getattr(obj,key);saved=_clone(original)
            if saved is not _SKIP:self.items.append((obj,key,original,saved,label))
        for key in ['x','y','f','g','b','t']:take(system.dae,key,'dae.'+key)
        for key in self.TDS_FIELDS:take(system.TDS,key,'tds.'+key)
        for name,model in system.exist.pflow_tds.items():
            if not model.n:continue
            if name not in self.MODELS:raise ValueError('unsupported native checkpoint model: '+name)
            for collection in ['services','services_post','num_params']:
                for key,service in getattr(model,collection).items():take(service,'v',name+'.'+collection+'.'+key)
            for key,block in model.discrete.items():
                kind=type(block).__name__
                if kind not in self.DISCRETE:raise ValueError('unsupported native checkpoint discrete: '+kind)
                self.discrete_inventory[name+'.'+key]=kind
                for attr in list(vars(block)):take(block,attr,name+'.discrete.'+key+'.'+attr)
        self.layout=[(obj,key,label) for obj,key,_,_,label in self.items]
        self._expected_digest=None

    def capture(self):
        items=[]
        for obj,key,label in self.layout:
            original=getattr(obj,key);saved=_clone(original)
            if saved is _SKIP:raise ValueError('native checkpoint value changed type: '+label)
            items.append((obj,key,original,saved,label))
        self.items=items;self._expected_digest=None
        return self

    @property
    def expected_digest(self):
        if self._expected_digest is None:
            h=hashlib.sha256()
            for _,_,_,saved,label in self.items:h.update(label.encode());h.update(_hash(saved))
            self._expected_digest=h.hexdigest()
        return self._expected_digest

    def digest(self):
        h=hashlib.sha256()
        for obj,key,_,_,label in self.items:
            value=_clone(getattr(obj,key))
            if value is _SKIP:raise ValueError('native checkpoint value changed type: '+label)
            h.update(label.encode());h.update(_hash(value))
        return h.hexdigest()

    def restore(self):
        for obj,key,original,saved,label in self.items:
            if isinstance(original,np.ndarray):
                if original.shape!=saved.shape:raise ValueError('native rollback resized an array: '+label)
                if getattr(obj,key) is not original:setattr(obj,key,original)
                original[...] = saved
            else:setattr(obj,key,copy.deepcopy(saved))
        self.system.vars_to_models()
        self.system.TDS.solver.worker.factorize=True
        if self.digest()!=self.expected_digest:raise ValueError('native checkpoint did not restore full audited state')

    def arrays_keep_identity(self):
        return all(getattr(obj,key) is original for obj,key,original,_,_ in self.items if isinstance(original,np.ndarray))

"""Sparse local differential blocks and exact nonassembled network Schur action.

Stores f_x at fixed V (device-local sparse blocks), f_V (two columns per
local port), b_x (sparse Norton injection) and the current network LU. The dense
reduced matrix f_x + f_V Y^-1 b_x is never formed. No numerical thresholding.
"""
import numpy as np
from scipy.sparse import coo_matrix
from power_grid_env.dae import injection_product,mode_token


def device_groups(model,dimension):
    groups=[]
    for i in range(len(model.settings.motors)):
        group=[3*i,3*i+1,3*i+2]
        if i in model.thermal_indices:group.append(model.thermal_indices[i])
        if i in model.cages.addresses:
            a=model.cages.addresses[i];group.extend(range(a.start,a.stop))
        groups.append((group,int(model.motor_buses[i])))
    for i in range(len(getattr(model,'generators',[]))):
        group=[model.gen_offset+2*i,model.gen_offset+2*i+1]
        for addresses in [model.round_rotors.addresses,model.controllers.pss_addresses]:
            if i in addresses:
                a=addresses[i];group.extend(range(a.start,a.stop))
        for addresses in [model.controllers.gov,model.controllers.avr]:
            if i in addresses:group.extend(addresses[i])
        for name,count in [('generator_thermal',1),('field_thermal',1),('excitation_limiters',2),('underexcitation_limiters',2),('oel2c',8),('esst1a',5),('takeover_oel',2)]:
            entries=getattr(getattr(model,name,None),'entries',{})
            if i in entries:
                a=entries[i][0];group.extend(range(a,a+count))
        groups.append((group,int(model.gen_buses[i])))
    flat=[j for g,bus in groups for j in g]
    if len(set(flat))!=len(flat) or any(j<0 or j>=dimension for j in flat):raise ValueError('invalid DAE device-state partition')
    groups.extend([([j],model.source_bus) for j in range(dimension) if j not in set(flat)])
    return [(np.asarray(g,dtype=int),bus) for g,bus in groups]


class DAEBlocks:
    def __init__(self,model,callback,time,state,maximum,check,tick):
        automation_callback=callback if hasattr(callback,'automation_physical_callback') else None
        if automation_callback is not None:callback=callback.automation_physical_callback
        self.n=len(state);self.mode=mode_token(model);self.lu=model.lu;self.maximum=maximum
        self.owners=np.empty(self.n,dtype=int);groups=device_groups(model,self.n);ctx=callback.jacobian_context(time,state)
        nodes=len(model.bus_names);ar=[];ac=[];av=[];cr=[];cc=[];cv=[]
        for indices,bus in groups:
            if len(indices)>64:raise ValueError('unsupported DAE device-state block larger than 64')
            self.owners[indices]=bus;outside=np.ones(self.n,dtype=bool);outside[indices]=False
            for first in range(0,len(indices),8):
                check();cols=indices[first:first+8];u=np.zeros((self.n,len(cols)));u[cols,np.arange(len(cols))]=1.
                values=callback.dae_actions(time,state,u,ctx,voltage_directions=np.zeros((nodes,len(cols)),complex));tick()
                if values.shape!=u.shape or not np.isfinite(values).all() or np.any(values[outside]!=0):raise ValueError('nonlocal fixed-voltage differential block outside DAE profile')
                r,c=np.nonzero(values[indices]);ar.extend(indices[r]);ac.extend(cols[c]);av.extend(values[indices[r],c])
                inj=injection_product(model,state,u);r,c=np.nonzero(inj);cr.extend(r);cc.extend(cols[c]);cv.extend(inj[r,c])
        self.A=coo_matrix((av,(ar,ac)),shape=(self.n,self.n)).tocsr()
        self.C=coo_matrix((cv,(cr,cc)),shape=(nodes,self.n),dtype=complex).tocsr()
        self.B=callback.dae_actions(time,state,np.zeros((self.n,2)),ctx,voltage_directions=np.tile([1.+0j,1j],(nodes,1)));tick()
        if self.B.shape!=(self.n,2) or not np.isfinite(self.B).all():raise ValueError('invalid local voltage differential block')
        buses=np.unique(self.owners);cached=getattr(model,'_dae_port_diagonal',None)
        if cached is None or cached[0] is not model.lu or not np.array_equal(cached[1],buses):
            diagonal=np.zeros(len(buses),complex);dead=set(getattr(model.lu,'dead',[]))
            for first in range(0,len(buses),8):
                check();positions=[j for j in range(first,min(len(buses),first+8)) if buses[j] not in dead]
                selected=buses[positions];rhs=np.zeros((nodes,len(selected)),complex);rhs[selected,np.arange(len(selected))]=1.
                if len(selected):diagonal[positions]=self.lu.solve(rhs)[selected,np.arange(len(selected))]
            cached=(model.lu,buses,diagonal);model._dae_port_diagonal=cached
        diag=dict(zip(cached[1],cached[2]));self.blocks=[]
        for indices,bus in groups:
            for first in range(0,len(indices),maximum):
                selected=indices[first:first+maximum];local=self.A[selected,:][:,selected].toarray()
                dv=diag[bus]*self.C[bus,selected].toarray().ravel()
                local+=self.B[selected,0,None]*dv.real[None,:]+self.B[selected,1,None]*dv.imag[None,:]
                self.blocks.append((selected,local))
        self.stored_local_scalar_slots=int(self.A.nnz+self.C.nnz*2+self.B.size+sum(b.size for _,b in self.blocks))
        if automation_callback is not None:
            from power_grid_env.automation_jacobian import extend_blocks
            extend_blocks(self,model,automation_callback,time,state,check,tick)

    def action(self,vector):
        v=np.asarray(vector).reshape(self.n)
        if np.iscomplexobj(v):
            all_voltage=self.lu.solve(self.C@np.column_stack([v.real,v.imag]));voltage=all_voltage[self.owners]
            network=self.B[:,0]*(voltage[:,0].real+1j*voltage[:,1].real)+self.B[:,1]*(voltage[:,0].imag+1j*voltage[:,1].imag)
            if hasattr(self,'automation_voltage'):
                network+=self.automation_voltage@np.r_[all_voltage[:,0].real+1j*all_voltage[:,1].real,all_voltage[:,0].imag+1j*all_voltage[:,1].imag]
        else:
            all_voltage=self.lu.solve(self.C@v);voltage=all_voltage[self.owners];network=self.B[:,0]*voltage.real+self.B[:,1]*voltage.imag
            if hasattr(self,'automation_voltage'):network+=self.automation_voltage@np.r_[all_voltage.real,all_voltage.imag]
        return self.A@v+network

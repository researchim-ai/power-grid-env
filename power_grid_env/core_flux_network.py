"""Flux-state Gamma network with an eddy-current conductance at every motor.

The published core branch carries Gc*d(psi_s)/dt. Its current is excluded from
electromagnetic torque. Conductive node rows use original KCL; purely inductive
junction rows use differentiated KCL and preserve their zero initial invariant.
No artificial grounding or capacitance is used. Mixed Gc=0/Gc>0 fleets are not
part of this explicit profile.
"""
import math
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from power_grid_env.flux_network import FluxNetwork


class CoreFluxNetwork(FluxNetwork):
    def __init__(self,network,settings):
        if not all(p.core_loss_s>0 for p in settings.motors):raise ValueError('core-loss network requires positive Gc at every motor; mixed fleets unsupported')
        self.conductive_ports=[]
        super().__init__(network,settings)

    def transformer_parameters(self,tr):
        from power_grid_env.rms_study import transformer_parameters
        copy=tr.model_copy(deep=True);copy.imag_pct=0.;copy.no_load_loss_pct=0.
        return transformer_parameters(copy,self.topology,self.network.s_base_va)

    def transformer_excitation(self,tr,bus,branch):
        first=tr.windings[0];loss=tr.no_load_loss_pct or 0.;idle=tr.imag_pct or 0.
        if idle<loss:raise ValueError('transformer no-load VA must cover active core loss')
        voltage=first.nominal_ll_v*first.tap_pu;g=first.rated_va*loss/100/voltage**2
        if g:
            ids={x.id for group in [self.network.sources,self.network.lines,self.network.loads,self.network.transformers,self.settings.motors] for x in group}
            if tr.id+'.core' in ids:raise ValueError('transformer excitation output ID collides with equipment')
            self.conductive_ports.append((tr.id+'.core',bus,g))
        q=first.rated_va/100*math.sqrt(max(0.,idle**2-loss**2))
        if q:branch('@magnetizing:'+tr.id,bus,None,0.,voltage**2/(self.wb*q))

    def resistive_load(self,load,bus,conductance):
        if conductance<=0:raise ValueError('positive resistive load required')
        self.conductive_ports.append((load.id,bus,conductance))

    def additional_flux_channels(self,voltage):
        result={}
        for id_,bus,g in self.conductive_ports:
            current=g*voltage[bus]
            result[id_+'.current_alpha_a']=current.real;result[id_+'.current_beta_a']=current.imag
            result[id_+'.loss_w']=float(1.5*g*abs(voltage[bus])**2)
        return result

    def quantities(self,time,state):
        if not hasattr(self,'inductive_k'):
            from scipy.sparse import diags
            self.inductive_k=(self.B@diags(1/self.L)@self.B.conj().T).toarray()
        n=len(self.names);edge=self.complex_values(state[:2*len(self.L)])
        outgoing=np.asarray(self.B@edge).ravel();g=np.zeros(n);injection=np.zeros(n,complex);data=[]
        for _,bus,conductance in self.conductive_ports:g[bus]+=conductance
        for model,bus,(address,speed,temp) in zip(self.motors,self.motor_buses,self.addresses):
            p=model.p;flux=self.complex_values(state[address]);psi=flux[0];rotor=flux[1:]
            temperature=state[temp:temp+2] if temp is not None else None
            factors=1+p.thermal.alpha_per_c*(temperature-p.thermal.reference_c) if p.thermal else np.ones(2)
            if np.min(factors)<=0 or (p.thermal and max(temperature)>p.thermal.maximum_c):raise ValueError('winding temperature outside passport range')
            rs=p.stator_r_ohm*factors[0];rr=np.array([c.resistance_ohm for c in p.cages])*factors[1]
            leakage=np.array([c.leakage_h for c in p.cages]);ir=(rotor-psi)/leakage
            sat=(p.saturation_beta_per_vs*abs(psi))**p.saturation_exponent
            magnetic=psi*(1+sat)/p.unsaturated_h-sum(ir)
            denominator=1+p.core_loss_s*rs;conductance=p.core_loss_s/denominator;j=magnetic/denominator
            g[bus]+=conductance;injection[bus]+=j
            data.append((psi,rotor,ir,magnetic,rs,rr,leakage,temperature,conductance,j,sat))
        # Current rows contain no nodal off-diagonal conductance. The other
        # rows express d(KCL)/dt through the physical RL branch inductances.
        matrix=self.inductive_k.copy();rhs=np.asarray(self.B@(self.R/self.L*edge)).ravel()
        for bus in range(n):
            if g[bus]>0:matrix[bus]=0.;matrix[bus,bus]=g[bus];rhs[bus]=-outgoing[bus]-injection[bus]
        source_data=[]
        for source,bus,index in self.sources:
            emf=source.ll_v*math.sqrt(2/3)*self.source_scales[source.id]*np.exp(1j*(self.wb*time+source.angle_a_rad))
            matrix[bus]=0.
            if index is None:
                matrix[bus,bus]=1+source.r_ohm*g[bus];rhs[bus]=emf-source.r_ohm*(outgoing[bus]+injection[bus])
            else:matrix[bus,bus]=1.;rhs[bus]=emf
            source_data.append((source,bus,index,emf))
        lu=splu(csc_matrix(matrix));self.factorizations+=1;voltage=lu.solve(rhs);rows=[]
        for model,bus,(_,speed,temp),values in zip(self.motors,self.motor_buses,self.addresses,data):
            p=model.p;psi,rotor,ir,magnetic,rs,rr,leakage,temperature,conductance,j,sat=values
            current=j+conductance*voltage[bus];dpsi=voltage[bus]-rs*current;core=1.5*p.core_loss_s*abs(dpsi)**2
            energy=1.5/p.unsaturated_h*(abs(psi)**2/2+sat*abs(psi)**2/(p.saturation_exponent+2))+.75*sum(leakage*abs(ir)**2)
            q=dict(psi=psi,rotor_flux=rotor,energy_j=float(energy),rs=rs,rr=rr,torque_nm=float(1.5*p.pole_pairs*(magnetic*np.conj(psi)).imag),
                stator_copper_w=float(1.5*rs*abs(current)**2),rotor_copper_w=float(1.5*sum(rr*abs(ir)**2)),core_loss_w=float(core),
                stator_flux_derivative=dpsi,rotor_flux_derivative=-rr*ir+1j*p.pole_pairs*state[speed]*rotor)
            rows.append((np.r_[current,ir],temperature,q));outgoing[bus]+=current
        passive_loss=0.
        for _,bus,conductance in self.conductive_ports:
            outgoing[bus]+=conductance*voltage[bus];passive_loss+=1.5*conductance*abs(voltage[bus])**2
        pin=source_resistor=0.;source_currents={}
        for source,bus,index,emf in source_data:
            current=outgoing[bus] if index is None else edge[index];source_currents[source.id]=current
            pin+=1.5*(emf*np.conj(current)).real
            if index is None:source_resistor+=1.5*source.r_ohm*abs(current)**2
        residual=outgoing[self.interior];kcl=float(max(abs(residual),default=0.)/max(1.,max(abs(outgoing),default=0.),max(abs(edge),default=0.)))
        return edge,rows,voltage,float(pin),float(source_resistor+passive_loss),source_currents,kcl

    def rhs(self,time,state):
        edge,rows,voltage,pin,source_r,_,_=self.quantities(time,state);derivative=np.zeros_like(state)
        derivative[:2*len(self.L)]=np.column_stack([((np.asarray(self.B.conj().T@voltage).ravel()-self.R*edge)/self.L).real,
                                                  ((np.asarray(self.B.conj().T@voltage).ravel()-self.R*edge)/self.L).imag]).ravel()
        mechanical=ambient=external=0.
        for model,(address,speed,temp),(currents,temperature,q) in zip(self.motors,self.addresses,rows):
            p=model.p;flux_rate=np.r_[q['stator_flux_derivative'],q['rotor_flux_derivative']]
            derivative[address]=np.column_stack([flux_rate.real,flux_rate.imag]).ravel()
            load=p.fan_nm_per_rad_s2*state[speed]*abs(state[speed])+p.viscous_nm_per_rad_s*state[speed]
            derivative[speed]=0. if p.locked_rotor else (q['torque_nm']-load)/p.inertia_kg_m2
            mechanical+=load*state[speed]
            losses=q['stator_copper_w']+q['rotor_copper_w']+q['core_loss_w']
            if temp is None:external+=losses
            else:
                h=p.thermal;ts,tr=temperature;cross=h.cross_w_per_k*(ts-tr)
                hs=h.stator_ambient_w_per_k*(ts-h.ambient_c);hr=h.rotor_ambient_w_per_k*(tr-h.ambient_c)
                derivative[temp]=(q['stator_copper_w']+q['core_loss_w']-hs-cross)/h.stator_capacity_j_per_k
                derivative[temp+1]=(q['rotor_copper_w']-hr+cross)/h.rotor_capacity_j_per_k;ambient+=hs+hr
        derivative[self.dynamic_end:]=[pin,source_r+1.5*float(sum(self.R*abs(edge)**2)),mechanical,ambient,external]
        if not np.isfinite(derivative).all():raise ValueError('nonfinite core-loss network derivatives')
        return derivative

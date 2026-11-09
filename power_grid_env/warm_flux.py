"""Sealed warm full-winding studies and bounded machine-state port export."""
from pathlib import Path
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.model import Network,canonical_hash
from power_grid_env.flux_network import FluxNetworkSettings,FluxNetwork,run_flux_network
from power_grid_env.flux_initialization import assess_periodic,phase_ports
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class WarmFluxStudy(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    network:Network
    settings:FluxNetworkSettings
    phase_terminals:dict[str,tuple[str,str,str]]=Field(default_factory=dict,max_length=8)

    @model_validator(mode='after')
    def bounded(self):
        if self.settings.operating_point is None:raise ValueError('explicit periodic operating point required')
        if len(self.settings.motors)>8:raise ValueError('at most eight warm motors')
        if self.phase_terminals and set(self.phase_terminals)!={m.id for m in self.settings.motors}:raise ValueError('phase mapping must cover every motor exactly once')
        if len(encoded(self.model_dump(mode='json')))>8000000:raise ValueError('warm study input budget')
        return self


def summary_for(study,result):
    complete=result.get('completed') is True
    if complete:
        t=np.asarray(result['time_s'],float);sides=result['sample_side'];channels=result['channels']
        if t.ndim!=1 or len(t)<2 or len(sides)!=len(t) or not np.isfinite(t).all() or np.any(np.diff(t)<0) or t[0]!=0 or abs(t[-1]-study.settings.duration_s)>1e-12 or max(np.diff(t))>study.settings.step_s+1e-12:
            raise ValueError('incomplete warm trajectory axis')
        for name,values in channels.items():
            a=np.asarray(values,float)
            if a.shape!=t.shape or not np.isfinite(a).all():raise ValueError('invalid warm trajectory channel: '+name)
        for metric,channel in [('maximum_kcl_relative_residual','kcl_relative_residual'),('maximum_energy_relative_residual','energy_relative_residual')]:
            if result[metric]!=max(channels[channel]) or min(channels[channel])<0:raise ValueError('trajectory diagnostic maximum differs')
    qualified=bool(complete and result.get('initialization',{}).get('qualified') and
        result['maximum_kcl_relative_residual']<=study.settings.maximum_kcl_relative_residual and
        result['maximum_energy_relative_residual']<=study.settings.maximum_energy_relative_residual)
    return dict(completed=complete,numerically_qualified=qualified,producer_sha256=result.get('code_sha256'),
        reason=result.get('detail',result.get('reason')),initial_state='synchronous_periodic',
        phase_network_transferred=False,arbitrary_RMS_state_imported=False,stability_proven=False,
        verification_scope='stored trajectory integrity and recomputed initial network/shaft equilibrium; no trajectory reintegration')


def run_warm_flux(document,out):
    study=WarmFluxStudy.model_validate(document);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(study.model_dump(mode='json')))
    result=run_flux_network(study.network,study.settings)
    (out/'result.json').write_bytes(encoded(result))
    ports=phase_ports(study.settings,result['initialization'],study.phase_terminals) if result.get('completed') and study.phase_terminals else []
    (out/'phase-ports.json').write_bytes(encoded([p.model_dump(mode='json') for p in ports]))
    summary=summary_for(study,result);(out/'summary.json').write_bytes(encoded(summary));seal(out)
    return summary


def verify_warm_flux(root):
    root=Path(root);identity=verify(root);study=WarmFluxStudy.model_validate(read_json(root/'input.json'));result=read_json(root/'result.json')
    if not result.get('completed') or result.get('code_sha256')!=package_fingerprint() or result.get('network_sha256')!=canonical_hash(study.network) or result.get('settings')!=study.settings.model_dump(mode='json'):
        raise ValueError('complete current-producer warm study identity required')
    circuit=FluxNetwork(study.network,study.settings);state=circuit.initial_state();initial=result['initialization']
    branches=initial['branch_currents_ab_a']
    if set(branches)!=set(circuit.branch_ids) or set(initial['motors'])!={p.id for p in study.settings.motors}:raise ValueError('initial state inventory differs')
    for i,key in enumerate(circuit.branch_ids):state[2*i:2*i+2]=branches[key]
    for model,(a,speed,_) in zip(circuit.motors,circuit.addresses):
        data=initial['motors'][model.p.id];values=data['currents_ab_a']
        if len(values)!=len(model.p.cages)+1:raise ValueError('initial winding inventory differs')
        state[a]=np.array(values).ravel();state[speed]=data['speed_rad_s']
    audit=assess_periodic(circuit,state,study.settings.operating_point)
    if not audit['qualified'] or any(initial.get(k)!=v for k,v in audit.items()):raise ValueError('stored periodic equilibrium differs from recomputed physics')
    # Tie the audited state to actual exported initial trajectory samples.
    channels=result['channels']
    for model,(_,speed,_) in zip(circuit.motors,circuit.addresses):
        current=initial['motors'][model.p.id]['currents_ab_a'][0]
        for key,value in [('speed_rad_s',state[speed]),('current_alpha_a',current[0]),('current_beta_a',current[1])]:
            if channels[model.p.id+'.'+key][0]!=value:raise ValueError('initial trajectory differs from audited state')
    ports=phase_ports(study.settings,initial,study.phase_terminals) if study.phase_terminals else []
    if read_json(root/'phase-ports.json')!=[p.model_dump(mode='json') for p in ports]:raise ValueError('exported phase state differs from audited state')
    summary=summary_for(study,result)
    if summary!=read_json(root/'summary.json'):raise ValueError('warm summary differs')
    return dict(verified=True,manifest_sha256=identity,**summary)

"""Declared 30-s mixed PSS/OEL2C/UEL mesh, original passports retained."""
from benchmarks.sparse_jacobian_validation import case as original
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec


def case():
    s=original('max_genrou_long_8_32');s.id='joint-long-512-32-8-30s';p=s.coupled
    p.duration_s=30.;p.step_s=.1;p.monitored_lines=[];p.rtol=1e-9;p.atol=1e-11;p.event_policy='coincident_roots_v1';p.event_trace='sampled_controls'
    p.jacobian_format='csc';p.jacobian_network='direct_solve';p.runtime_limits.maximum_integration_wall_s=900.
    p.events += [CoupledEvent(time_s=t,kind='source_voltage',equipment_id=s.network.sources[0].id,value=v) for t,v in ((8.,.7),(9.,1.),(18.,.8),(19.,1.))]
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    p.underexcitation_limiters=[UnderexcitationLimiter(generator_id=q.generator_id,Q_0=.1,Radius=.15,K_M=2.,T_M=.1,K_F2=.05,T_F2=.2,MEL_MAX=.06) if q.generator_id=='g2' else q for q in p.underexcitation_limiters]
    p.events += [CoupledEvent(time_s=t,kind='voltage_reference_step',equipment_id='g2',value=value) for t,value in ((23.,-.04),(26.,.04))]
    return StudySpec.model_validate(s.model_dump(mode='json'))

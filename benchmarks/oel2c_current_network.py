"""Independent SI stationary field constraint for the joint current/PV root."""
import cmath
from scipy.optimize import root
from benchmarks.genrou_reference import saturation_curve
from benchmarks.group_si_generator import Generator


def stationary_field(network,settings,voltage,current):
    g=Generator.__new__(Generator);g._configure(network,settings);p=g.p
    emf=voltage+g.z*current;strength=saturation_curve(p,abs(emf)/g.vbase)
    def residual(delta):
        flux=emf/g.vbase*cmath.exp(-1j*delta[0]);own=current/g.ibase*cmath.exp(-1j*delta[0])
        return [(1+strength*g.gqd)*(-flux.imag)-(p.x_q_pu-p.x_q_subtransient_pu)*own.real]
    guess=cmath.phase(voltage/g.vbase+complex(p.armature_r_pu,p.x_q_pu)*current/g.ibase)
    solved=root(residual,[guess],tol=1e-11)
    if abs(residual(solved.x)[0])>1e-9:raise ValueError('independent stationary field angle root failed')
    rotation=cmath.exp(-1j*solved.x[0]);flux=emf/g.vbase*rotation;own=current/g.ibase*rotation
    return (1+strength)*flux.real+(p.x_d_pu-p.x_d_subtransient_pu)*(-own.imag)

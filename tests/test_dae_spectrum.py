"""Critical-mode products agree with the exact small dense Jacobian."""
import numpy as np
import pytest
from power_grid_env.dae_spectrum import critical_modes
from tests.test_sparse_jacobian import profile


def equilibrium():
    m,x=profile('round');indices=[]
    for i in range(len(m.generators)):
        indices.extend([m.gen_offset+2*i,m.gen_offset+2*i+1]);a=m.round_rotors.addresses[i];indices.extend(range(a.start,a.stop))
        if i in m.controllers.gov:indices.extend(m.controllers.gov[i])
        if i in m.controllers.avr:indices.extend(m.controllers.avr[i])
    return m,x,np.asarray(indices,dtype=int)


def test_sparse_computed_modes_match_dense_reference():
    m,x,indices=equilibrium();r=critical_modes(m,x,indices=indices,count=2);dense=m.integration_jacobian()(0.,x)[np.ix_(indices,indices)];values=np.linalg.eigvals(dense)
    assert r['completed'] and not r['global_or_hybrid_stability_certified']
    for value in r['eigenvalues']:assert min(abs(values-complex(value['real'],value['imag'])))<1e-6
    assert max(r['eigenpair_relative_residuals'])<=1e-7


def test_non_equilibrium_and_product_budget_fail_closed():
    m,x=profile('motor')
    with pytest.raises(ValueError,match='equilibrium'):critical_modes(m,x,count=1)
    m,x,indices=equilibrium()
    with pytest.raises(ValueError,match='budget'):critical_modes(m,x,indices=indices,count=2,maximum_products=1)


@pytest.mark.parametrize('bad',[[0,0],[-1],[100000],[.5]])
def test_bad_mode_subset_rejected(bad):
    m,x,indices=equilibrium()
    with pytest.raises(ValueError):critical_modes(m,x,indices=bad,count=1)

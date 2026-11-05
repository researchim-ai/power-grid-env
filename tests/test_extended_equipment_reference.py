import copy,os
from pathlib import Path
import pytest
from benchmarks.extended_equipment_validation import example,exact_reference,native_reference,compare,verify_native,NAMES
from power_grid_env.flux_network import run_flux_network

@pytest.mark.parametrize('name',[n for n in NAMES if n.startswith('exact_')])
def test_full_winding_network_matches_exact_matrix_exponential(name):
    net,settings=example(name);reference=exact_reference(net,settings)
    result=compare(run_flux_network(net,settings),reference);assert result['passed'],result

@pytest.mark.parametrize('name',[n for n in NAMES if not n.startswith('exact_')])
def test_full_winding_matches_pinned_original_motulator(name):
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('independent motulator wheel must be explicitly supplied')
    verify_native(Path(wheel));net,settings=example(name);reference=native_reference(net,settings)
    result=compare(run_flux_network(net,settings),reference);assert result['passed'],result

@pytest.mark.parametrize('defect',['nan','missing','axis','incomplete','error'])
def test_reference_comparator_rejects_invalid_or_inaccurate_trace(defect):
    net,settings=example('exact_one_cage');reference=exact_reference(net,settings);primary=run_flux_network(net,settings)
    key='m.current_alpha_a'
    if defect=='nan':primary['channels'][key][1]=float('nan')
    elif defect=='missing':del primary['channels'][key]
    elif defect=='axis':primary['time_s'][1]+=.00001
    elif defect=='incomplete':primary['completed']=False
    else:primary['channels'][key][1]+=1.
    if defect=='error':assert not compare(primary,reference)['passed']
    else:
        with pytest.raises(ValueError):compare(primary,reference)

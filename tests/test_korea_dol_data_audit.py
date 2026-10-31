import io
import numpy as np
import pytest
from scipy.io import savemat
from benchmarks.korea_dol_data_audit import inspect_member,audit


def mat(key='cur1',count=100000,value=0.):
    b=io.BytesIO();savemat(b,{key:np.full((count,1),value)});return b.getvalue()


def test_sensor_data_not_promoted_to_calibrated_si():
    r=inspect_member('Healthy_DOLstart_0_cur1.mat',mat(value=1.))
    assert r['maximum_raw']==1. and not r['si_units_verified'] and not r['calibration_applied']


@pytest.mark.parametrize('key,name',[('ac0','Ver'),('airgflk','airgflux'),('leakflk','leakflux')])
def test_publisher_variable_names_preserved(key,name):
    assert inspect_member('Healthy_DOLstart_0_'+name+'.mat',mat(key))['variable']==key


@pytest.mark.parametrize('content',[mat('wrong'),mat(count=99999),mat(value=float('nan'))],ids=['wrong-variable','wrong-shape','nonfinite'])
def test_invalid_variables_shape_and_nonfinite_rejected(content):
    with pytest.raises(ValueError):inspect_member('Healthy_DOLstart_0_cur1.mat',content)


def test_unpinned_archive_refused(tmp_path):
    p=tmp_path/'bad.zip';p.write_bytes(b'bad')
    with pytest.raises(ValueError,match='integrity'):audit(p,tmp_path/'out')
    assert not (tmp_path/'out').exists()

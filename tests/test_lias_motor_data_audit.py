import numpy as np
import pytest
from benchmarks.lias_motor_data_audit import inspect_record, audit


def record(speed=301.58):
    a=np.zeros((20,9));a[:,0]=np.arange(20)*.0007;a[:,7]=speed
    return ('\n'.join(','.join(map(str,row)) for row in a)).encode()


def test_documented_speed_conflict_is_not_silently_corrected():
    r=inspect_record(record(),(1432,1439))
    assert not r['speed_matches_documented_units']
    assert r['speed_factor_applied']==1.
    assert 2879 < r['rpm_if_documented_rad_s'] < 2881


def test_valid_speed_units_are_recognized():
    r=inspect_record(record(1435*2*np.pi/60),(1432,1439))
    assert r['speed_matches_documented_units']


@pytest.mark.parametrize('content',[b'1,2,3\n'*20,record().replace(b'0.0007,',b'0.0,',1),record().replace(b'301.58',b'nan',1)])
def test_invalid_channel_or_clock_or_nonfinite_rejected(content):
    with pytest.raises(ValueError):inspect_record(content,(1432,1439))


def test_unpinned_archive_rejected(tmp_path):
    p=tmp_path/'bad.tar.gz';p.write_bytes(b'bad')
    with pytest.raises(ValueError,match='integrity'):audit(p,tmp_path/'out')
    assert not (tmp_path/'out').exists()

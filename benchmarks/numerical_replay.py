"""Tight physical-value replay for SciPy's mixed-equation root reference.

JSON structure, booleans, integer counters and strings remain exact. Finite
floating values allow 5e-10 absolute roundoff, no relative relaxation; time axes
and event timestamps allow only 5e-13 seconds. These limits are orders below
physical acceptance caps and never used for original primary/reference checks.
"""
import math

ABSOLUTE=5e-10
TIME_ABSOLUTE=5e-13


def assert_replay_equal(saved,current,path=()):
    if isinstance(saved,dict) and isinstance(current,dict):
        if saved.keys()!=current.keys():raise ValueError('replay object inventory differs: '+str(path))
        for key in saved:assert_replay_equal(saved[key],current[key],path+(key,))
    elif isinstance(saved,list) and isinstance(current,list):
        if len(saved)!=len(current):raise ValueError('replay array inventory differs: '+str(path))
        for index,(a,b) in enumerate(zip(saved,current)):assert_replay_equal(a,b,path+(index,))
    elif isinstance(saved,float) and isinstance(current,float):
        tolerance=TIME_ABSOLUTE if 'time_s' in path else ABSOLUTE
        if not math.isfinite(saved) or not math.isfinite(current) or abs(saved-current)>tolerance:raise ValueError('replay physical value differs: '+str(path))
    elif type(saved) is not type(current) or saved!=current:raise ValueError('replay discrete value differs: '+str(path))

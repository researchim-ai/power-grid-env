"""Named root-vector contracts for the pinned native MNLEX2 adapter."""
POLICIES=('compound_and_atoms','continuous_atoms')
ATOMIC_INDICATORS=('raw_lower_bound','raw_upper_bound','lag_rate_lower_inward','lag_rate_upper_inward')


def indicator_names(policy,native_count):
    if policy not in POLICIES:raise ValueError('unknown native UEL root policy')
    if type(native_count) is not int or native_count<0:raise ValueError('nonnegative native indicator count required')
    prefix=tuple('native_compound_'+str(i) for i in range(native_count)) if policy=='compound_and_atoms' else ()
    return prefix+ATOMIC_INDICATORS


def indicator_directions(policy,native_count):
    """Only boundary entry and inward-rate entry can enable original when.

    Returning inside a state bound after reinit cannot enable a strict outside
    guard. Filtering that crossing avoids zero-time re-detection at the reset.
    Boolean compound legacy indicators retain bidirectional diagnostics.
    """
    indicator_names(policy,native_count)
    if policy=='compound_and_atoms':return (0,)*(native_count+4)
    return (-1,1,1,-1)

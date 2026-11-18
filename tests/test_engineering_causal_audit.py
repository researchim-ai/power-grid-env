import pytest
import torch
from benchmarks.engineering_causal_audit import compare


def test_causal_metric_reports_both_numeric_error_and_decision_change():
    a=torch.tensor([1.,2.,3.]);b=torch.tensor([1.,4.,3.]);r=compare(a,b)
    assert r['maximum_absolute_difference']==2. and r['top_token_equal'] is False
    assert compare(a,a)==dict(maximum_absolute_difference=0.,top_token_equal=True)


@pytest.mark.parametrize('other',[torch.tensor([1.,2.]),torch.tensor([1.,float('nan'),3.])])
def test_invalid_causal_comparison_cannot_be_reported_as_consistent(other):
    with pytest.raises(ValueError):compare(torch.tensor([1.,2.,3.]),other)

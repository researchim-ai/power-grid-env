from benchmarks.engineering_skill_diagnose import content


def test_fenced_readiness_is_diagnostic_only():
    r=content('```json\n{"solution_ready":true}\n```',{'solution_ready':True},'readiness')
    assert r['diagnostic_content_exact'] and not r['strict']['exact']
    assert r['operational_score_unchanged']


def test_binding_wrapper_can_preserve_values_but_still_violate_contract():
    r=content('{"operation":"add_line","arguments":{"route_id":"R1","catalog_id":"copper-1"}}',
              {'route_id':'R1','catalog_id':'copper-1'},'binding')
    assert r['binding_wrapper'] and r['diagnostic_content_exact'] and not r['strict']['exact']


def test_wrong_value_and_free_prose_are_never_rescued():
    assert not content('The answer is {"value":"10"}',{'value':'10'},'perception')['diagnostic_content_exact']
    assert not content('```json\n{"value":"20"}\n```',{'value':'10'},'perception')['diagnostic_content_exact']

"""Local synthetic-corpus acceptance. Physics is OpenDSS, not an independent oracle."""
from pathlib import Path
import argparse

from power_grid_env.llm_training import (generate_corpus,verify_corpus,export_public_dataset,
    CorpusVerifier,teacher_episode,evaluate_predictions,_read_lines)
from power_grid_env.study_artifacts import encoded,export_zip,extract_zip,seal,read_json


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('new acceptance directory required')
    out.mkdir(parents=True);corpus=out/'corpus'
    generation=generate_corpus(dict(seed=20261108,variants_per_family=6),corpus)
    checked=verify_corpus(corpus);verifier=CorpusVerifier(corpus,allowed_splits=('train','dev','test'))
    groups=[];heldout=[]
    for identifier,task in sorted(verifier._tasks.items()):
        teacher=read_json(corpus/'private/episodes'/identifier/'teacher.json')
        text=encoded(teacher['plan']).decode();hold='{"actions":[]}'
        result=verifier.group(identifier,[text,hold,'{"actions":[],"reward":999}'])
        assert result['status']=='ok' and result['results'][0]['feasible'] and result['rewards'][0]>=result['rewards'][1]
        assert result['rewards'][2]==-1 and result['informative']
        assert result['results'][0]['restored']==(not task.network.switches[-1].locked and task.network.lines[-1].rating_a>1)
        zero=verifier.group(identifier,[text,text]);assert not zero['informative'] and zero['advantages']==[0,0]
        groups.append(dict(task_id=identifier,split=task.split,rewards=result['rewards'],advantages=result['advantages'],
                           feasible=result['results'][0]['feasible'],restored=result['results'][0]['restored']))
        if task.split=='test':heldout.append(dict(task_id=identifier,completion=text))
    evaluation=evaluate_predictions(corpus,heldout)
    assert evaluation['status']=='ok' and evaluation['feasible_fraction']==1
    exports={}
    for kind in ['plans','steps','tools','prompts']:
        exports[kind]=export_public_dataset(corpus,out/('train-'+kind),split='train',kind=kind)
        assert 'PRIVATE_ONLY_' not in (out/('train-'+kind)/'data.jsonl').read_text()
    transfer=export_zip(out/'train-plans',out/'train-plans.zip');extract_zip(out/'train-plans.zip',out/'transferred-plans')
    repeat=out/'repeated-corpus';generate_corpus(dict(seed=20261108,variants_per_family=6),repeat)
    assert (repeat/'manifest.json').read_bytes()==(corpus/'manifest.json').read_bytes()
    result=dict(schema='power-grid-env.llm-data-acceptance/v1',passed=True,tasks=checked['tasks'],
                counts=generation['counts'],producer_sha256=generation['producer_sha256'],groups=groups,
                heldout_teacher_evaluation=evaluation,exports=exports,transfer=transfer,
                deterministic_regeneration=True,independent_physics_oracle=False,trained_llm_quality_claimed=False)
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    args=parser.parse_args();r=run(args.out);print(encoded({k:r[k] for k in ['passed','tasks','counts','producer_sha256']}).decode())

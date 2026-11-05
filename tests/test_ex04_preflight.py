from types import SimpleNamespace
import pytest
from benchmarks.ex04_preflight import preflight,github_repository

@pytest.mark.parametrize('url',['git@github.com:researchim-ai/power-grid-env.git','https://github.com/researchim-ai/power-grid-env.git'])
def test_github_remote_normalization(url):assert github_repository(url)=='researchim-ai/power-grid-env'

@pytest.mark.parametrize('url',['https://secret@github.com/a/b','https://example.com/a/b','git@github.com:../b','https://github.com/a/b/extra'])
def test_credentials_and_non_github_remotes_rejected(url):
    with pytest.raises(ValueError):github_repository(url)

def test_preflight_never_promotes_readiness_to_remote_proof(tmp_path):
    commands=[]
    def run(argv,**kw):
        commands.append(argv)
        if argv[1:]==['rev-parse','HEAD']:text='a'*40
        elif argv[1:]==['branch','--show-current']:text='develop'
        elif argv[1:]==['remote','get-url','origin']:text='git@github.com:a/b.git'
        elif argv[1]=='status':text=''
        elif argv[1:3]==['attestation','verify']:text='--deny-self-hosted-runners --source-digest --signer-digest --signer-workflow --format --hostname'
        elif argv[1]=='api':text='a'*40
        else:text=''
        return SimpleNamespace(returncode=0,stdout=text)
    r=preflight(tmp_path,run)
    assert r['ready_to_request_remote_run'] and not r['ex04_complete'] and not r['signed_provenance_verified']
    assert all(not any(x in c for x in ['push','commit','dispatch']) for c in commands)

def test_missing_cli_and_dirty_worktree_fail_closed(tmp_path):
    def run(argv,**kw):
        if argv[1:]==['rev-parse','HEAD']:return SimpleNamespace(returncode=0,stdout='a'*40)
        if argv[1]=='status':return SimpleNamespace(returncode=0,stdout='?? source.py\0')
        if argv[0]=='gh':raise FileNotFoundError()
        return SimpleNamespace(returncode=1,stdout='')
    r=preflight(tmp_path,run);assert not r['ready_to_request_remote_run']
    assert 'working_tree_not_committed' in r['blockers'] and not r['github_authenticated']

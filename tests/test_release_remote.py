"""Synthetic policy/transport tests; these are not real signed remote-run receipts."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import zipfile
import pytest
from benchmarks.release_remote import MANDATORY_STEPS, DIAGNOSTIC_STEP, expected, metadata_contract, signature_contract, tree_contract, verify_remote
from benchmarks.release_archive import unpack, pack
from benchmarks.release_candidate import source_hashes
from benchmarks.release_verify import canonical, digest, inventory


@pytest.fixture
def bindings():
    e = expected('owner/project', 'a' * 40, 10, 2, 30)
    run = dict(id=10, run_attempt=2, head_sha=e['commit'], path='.github/workflows/release-candidate.yml',
               status='completed', conclusion='success', event='workflow_dispatch', head_branch='develop',
               repository=dict(id=50, full_name=e['repo']), head_repository=dict(id=50, full_name=e['repo']))
    jobs = dict(total_count=1, jobs=[dict(id=20, name='candidate', run_id=10, run_attempt=2, head_sha=e['commit'], status='completed', conclusion='success',
        started_at='2026-11-04T01:00:00Z', completed_at='2026-11-04T01:10:00Z', steps=[dict(name=n,status='completed',conclusion='success') for n in MANDATORY_STEPS]+[dict(name=DIAGNOSTIC_STEP,status='completed',conclusion='skipped')])])
    artifact = dict(id=30, name=f"ex04-linux-x86_64-py312-{e['commit']}-2", expired=False, size_in_bytes=123,
                    digest='sha256:' + 'b' * 64, created_at='2026-11-04T01:09:00Z',
                    workflow_run=dict(id=10, head_sha=e['commit'], repository_id=50, head_repository_id=50))
    return e, run, jobs, artifact


def test_metadata_contract_does_not_claim_authentication(bindings):
    r = metadata_contract(*bindings)
    assert r == dict(repository_id=50, job_id=20, ref='refs/heads/develop')
    assert 'independent_host_verified' not in r


@pytest.mark.parametrize('damage', ['run', 'attempt', 'commit', 'workflow', 'unfinished', 'failed', 'pull_request', 'fork', 'repo_id',
                                    'missing_job', 'partial_jobs', 'job_attempt', 'job_commit', 'job_failed', 'step_skipped',
                                    'artifact', 'expired', 'name_attempt', 'artifact_run', 'artifact_sha', 'artifact_repo', 'digest', 'size', 'time', 'timezone'])
def test_metadata_contract_rejects_misbound_proofs(bindings, damage):
    e, run, jobs, artifact = copy.deepcopy(bindings); job = jobs['jobs'][0]
    if damage == 'run': run['id'] = 11
    if damage == 'attempt': run['run_attempt'] = 1
    if damage == 'commit': run['head_sha'] = 'c' * 40
    if damage == 'workflow': run['path'] = '.github/workflows/other.yml'
    if damage == 'unfinished': run['status'] = 'in_progress'
    if damage == 'failed': run['conclusion'] = 'failure'
    if damage == 'pull_request': run['event'] = 'pull_request'
    if damage == 'fork': run['head_repository']['full_name'] = 'attacker/project'
    if damage == 'repo_id': run['head_repository']['id'] = 51
    if damage == 'missing_job': jobs['jobs'] = []
    if damage == 'partial_jobs': jobs['total_count'] = 2
    if damage == 'job_attempt': job['run_attempt'] = 1
    if damage == 'job_commit': job['head_sha'] = 'c' * 40
    if damage == 'job_failed': job['conclusion'] = 'failure'
    if damage == 'step_skipped': job['steps'][0]['conclusion'] = 'skipped'
    if damage == 'artifact': artifact['id'] = 31
    if damage == 'expired': artifact['expired'] = True
    if damage == 'name_attempt': artifact['name'] = artifact['name'][:-1] + '1'
    if damage == 'artifact_run': artifact['workflow_run']['id'] = 11
    if damage == 'artifact_sha': artifact['workflow_run']['head_sha'] = 'c' * 40
    if damage == 'artifact_repo': artifact['workflow_run']['head_repository_id'] = 51
    if damage == 'digest': artifact['digest'] = None
    if damage == 'size': artifact['size_in_bytes'] = 2 ** 40
    if damage == 'time': artifact['created_at'] = '2026-11-04T02:00:00Z'
    if damage == 'timezone': artifact['created_at'] = '2026-11-04T01:09:00'
    with pytest.raises(ValueError): metadata_contract(e, run, jobs, artifact)


def signature(bindings):
    e, run, jobs, artifact = bindings
    uri = 'https://github.com/' + e['repo']
    cert = {'issuer': 'https://token.actions.githubusercontent.com', 'runnerEnvironment': 'github-hosted',
            'sourceRepositoryURI': uri, 'sourceRepositoryDigest': e['commit'], 'sourceRepositoryIdentifier': '50',
            'sourceRepositoryRef': 'refs/heads/develop', 'buildSignerURI': uri + '/.github/workflows/release-candidate.yml@refs/heads/develop',
            'buildSignerDigest': e['commit'], 'buildTrigger': 'workflow_dispatch', 'runInvocationURI': uri + '/actions/runs/10/attempts/2'}
    return [{'verificationResult': {'signature': {'certificate': cert}, 'verifiedTimestamps': [{'type': 'tlog'}],
            'statement': {'predicateType': 'https://slsa.dev/provenance/v1', 'subject': [{'digest': {'sha256': 'd' * 64}}]}}}]


@pytest.mark.parametrize('field', ['issuer', 'runnerEnvironment', 'sourceRepositoryURI', 'sourceRepositoryDigest', 'sourceRepositoryIdentifier',
                                   'sourceRepositoryRef', 'buildSignerURI', 'buildSignerDigest', 'buildTrigger', 'runInvocationURI', 'timestamp', 'subject', 'predicate'])
def test_signed_certificate_checks_do_not_trust_predicate_or_runner_environment(bindings, field):
    e = bindings[0]; b = metadata_contract(*bindings); v = signature(bindings)
    assert signature_contract(e, b, v, 'd' * 64)['signed_runner_environment'] == 'github-hosted'
    result = v[0]['verificationResult']
    if field == 'timestamp': result['verifiedTimestamps'] = []
    elif field == 'subject': result['statement']['subject'][0]['digest']['sha256'] = 'e' * 64
    elif field == 'predicate': result['statement']['predicateType'] = 'custom'
    else:
        result['signature']['certificate'][field] = 'forged'
        result['statement']['predicate'] = {field: 'github-hosted', 'verified': True}
    with pytest.raises(ValueError): signature_contract(e, b, v, 'd' * 64)


@pytest.mark.parametrize('repo,sha,run,attempt,artifact', [('../project','a'*40,1,1,1),('owner/../project','a'*40,1,1,1),('owner/project','HEAD',1,1,1),('owner/project','a'*40,True,1,1),('owner/project','a'*40,1,0,1)])
def test_untrusted_locator_is_not_an_arbitrary_endpoint(repo, sha, run, attempt, artifact):
    with pytest.raises(ValueError): expected(repo, sha, run, attempt, artifact)


def tree(source):
    return {'truncated': False, 'tree': [dict(path=n, type='blob', mode='100644', size=len((source/n).read_bytes()),
        sha=hashlib.sha1(b'blob '+str(len((source/n).read_bytes())).encode()+b'\x00'+(source/n).read_bytes()).hexdigest()) for n in source_hashes(source)]}


@pytest.mark.parametrize('damage', [None, 'bytes', 'omission', 'extra', 'symlink', 'submodule', 'truncated', 'duplicate'])
def test_entire_source_closure_matches_committed_blobs(tmp_path, damage):
    (tmp_path / 'tests').mkdir(); (tmp_path / 'tests/test_a.py').write_text('data')
    t = tree(tmp_path)
    if damage == 'bytes': (tmp_path / 'tests/test_a.py').write_text('changed')
    if damage == 'omission': t['tree'] = []
    if damage == 'extra': t['tree'].append(dict(path='tests/test_other.py', type='blob', mode='100644', sha='0'*40, size=0))
    if damage == 'symlink': t['tree'][0]['mode'] = '120000'
    if damage == 'submodule': t['tree'][0]['type'] = 'commit'
    if damage == 'truncated': t['truncated'] = True
    if damage == 'duplicate': t['tree'].append(t['tree'][0])
    if damage:
        with pytest.raises(ValueError): tree_contract(t, tmp_path)
    else: assert tree_contract(t, tmp_path) == digest(canonical(source_hashes(tmp_path)))


@pytest.mark.parametrize('damage', ['traversal', 'absolute', 'backslash', 'duplicate', 'case', 'symlink', 'fifo', 'directory', 'prefix', 'outer_extra', 'budget'])
def test_archive_is_rejected_before_writing_payload(tmp_path, damage, monkeypatch):
    archive = tmp_path / 'bad.zip'
    name = {'traversal':'../escape', 'absolute':'/escape', 'backslash':'a\\b', 'directory':'a/'}.get(damage, 'candidate/a')
    with zipfile.ZipFile(archive, 'w') as z:
        i = zipfile.ZipInfo(name); i.create_system = 3
        i.external_attr = ((stat.S_IFLNK if damage == 'symlink' else stat.S_IFIFO if damage == 'fifo' else stat.S_IFREG) | 0o644) << 16
        z.writestr(i, b'payload')
        if damage in ('duplicate','case','prefix','outer_extra'):
            other = {'duplicate':name,'case':name.upper(),'prefix':name+'/x','outer_extra':'extra'}[damage]
            if damage == 'duplicate':
                with pytest.warns(UserWarning, match='Duplicate name'): z.writestr(other, b'other')
            else: z.writestr(other, b'other')
    if damage == 'budget': monkeypatch.setattr('benchmarks.release_archive.MAX_TOTAL', 1)
    with pytest.raises(ValueError): unpack(archive, tmp_path / 'out', only_file='release-payload.zip' if damage == 'outer_extra' else None)
    assert not (tmp_path / 'out').exists()
    assert not (tmp_path / 'escape').exists()


def test_old_cli_cannot_open_remote_gate(tmp_path, monkeypatch):
    def old(gh, args, out, *a, **k):
        Path(out).write_text('old gh without attestations')
    monkeypatch.setattr('benchmarks.release_remote.shutil.which', lambda name: '/trusted/gh')
    monkeypatch.setattr('benchmarks.release_remote._command', old)
    with pytest.raises(RuntimeError, match='lacks required'):
        verify_remote('owner/project','a'*40,10,2,30,tmp_path/'source',tmp_path/'report')
    assert json.loads((tmp_path/'report/failure.json').read_text())['independent_host_verified'] is False
    assert not (tmp_path/'report/verification.json').exists()


def test_payload_repeats_exactly_and_contains_hidden_source_without_execution(tmp_path):
    c = tmp_path / 'candidate'; (c / 'source/.github').mkdir(parents=True)
    (c / 'source/.github/workflow').write_text('must retain hidden directory')
    (c / 'verify.py').write_text('raise RuntimeError("NEVER EXECUTE DOWNLOADED CODE")')
    m = dict(schema='pge-release-candidate-v1',metadata={},files=inventory(c));m['candidate_id']=digest(canonical(m))
    (c/'manifest.json').write_text(json.dumps(m))
    evidence=tmp_path/'evidence';evidence.mkdir();(evidence/'tests.xml').write_text('<testsuites/>')
    (evidence/'test-inventory.json').write_text('{}')
    (evidence/'execution.json').write_text(json.dumps(dict(candidate_id=m['candidate_id'],local_candidate_checks_passed=True,test_inventory_sha256=digest(b'{}'))))
    a=pack(c,evidence,tmp_path/'a.zip');b=pack(c,evidence,tmp_path/'b.zip')
    assert a==b
    root=unpack(tmp_path/'a.zip',tmp_path/'out',allowed_prefixes={'candidate','evidence'})
    assert (root/'candidate/source/.github/workflow').is_file()
    assert (root/'candidate/verify.py').read_text().startswith('raise RuntimeError')


@pytest.mark.parametrize('damage', [None, 'signature_failure', 'artifact_digest', 'dirty_checkout', 'base_commit',
                                   'profile', 'inventory_skip', 'receipt_count', 'receipt_hash', 'code_metadata'])
@pytest.mark.parametrize('discover', [False, True])
def test_mocked_transport_pipeline_checks_every_binding(tmp_path, bindings, monkeypatch, damage, discover):
    """Mocked gh has NO cryptographic authority; exercises orchestration only."""
    import shutil
    from benchmarks.release_candidate import code_hash
    e, run, jobs, artifact = copy.deepcopy(bindings)
    source = tmp_path/'source'; source.mkdir()
    profile = {'platform': {'system':'Linux','machine':'x86_64','python':'3.12'}, 'required_test_files':['tests/test_a.py']}
    files = {'power_grid_env/__init__.py':'# fixture code\n', 'README.md':'fixture', 'LICENSE':'fixture license',
             'pyproject.toml':'fixture build inputs', 'requirements/pilot-linux-py312.lock':'# fixture lock\n',
             'requirements/release-balanced-dol-linux-py312.json':json.dumps(profile), 'tests/test_a.py':'def test_a(): pass',
             '.github/workflows/release-candidate.yml':'# fixture trusted workflow, not a real CI run\n'}
    for n, data in files.items():
        p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
    source_id=digest(canonical(source_hashes(source)));fp=code_hash([('__init__.py',files['power_grid_env/__init__.py'].encode())])
    c=tmp_path/'candidate';c.mkdir();shutil.copytree(source,c/'source');(c/'profile.json').write_text(json.dumps(profile))
    installation=c/'installation';installation.mkdir();wheel=installation/'power_grid_env-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as z:z.writestr('power_grid_env/__init__.py',files['power_grid_env/__init__.py'])
    lock=installation/'install-requirements.lock';lock.write_text('# fixture install lock\n')
    q=dict(clean_venvs=2,platform=dict(system='Linux',machine='x86_64',python='3.12.9'),code_sha256=fp,wheel_filename=wheel.name,
           wheel_sha256=digest(wheel.read_bytes()),runtime_lock_sha256=digest((source/'requirements/pilot-linux-py312.lock').read_bytes()),install_lock_sha256=digest(lock.read_bytes()))
    for key in ['repeat_build_same_sha256','offline_hash_checked_install','pip_check_passed','repository_independent_import','motor_replay_passed','opendss_smoke_passed','synthetic_trace_comparison_passed']:q[key]=True
    (installation/'qualification.json').write_text(json.dumps(q))
    build={n:digest((source/n).read_bytes()) for n in ['power_grid_env/__init__.py','README.md','LICENSE','pyproject.toml']}
    (installation/'build-inputs.json').write_text(json.dumps(build))
    (installation/'manifest.json').write_text(json.dumps({n:d['sha256'] for n,d in inventory(installation).items()}))
    if damage=='profile':(c/'profile.json').write_text(json.dumps({**profile,'unexpected':'different profile'}))
    metadata=dict(source_inventory_sha256=source_id,base_commit=e['commit'],working_tree_clean=True,code_sha256=fp)
    if damage=='dirty_checkout':metadata['working_tree_clean']=False
    if damage=='base_commit':metadata['base_commit']='c'*40
    if damage=='code_metadata':metadata['code_sha256']='f'*64
    m=dict(schema='pge-release-candidate-v1',metadata=metadata,files=inventory(c));m['candidate_id']=digest(canonical(m));(c/'manifest.json').write_text(json.dumps(m))
    evidence=tmp_path/'evidence';evidence.mkdir();(evidence/'tests.xml').write_text('<testsuites/>')
    tests=dict(schema='pge-test-inventory-v1',passed=True,pytest_exitstatus=0,collection_errors=[],profile_sha256=digest(canonical(profile)),source_inventory_sha256=source_id,
        collected_count=1,required_count=1,outside_profile_count=0,tests={'tests/test_a.py::test_a':dict(status='passed',phases={k:dict(outcome='passed',xfail=None) for k in ['setup','call','teardown']})})
    if damage=='inventory_skip':tests['tests']['tests/test_a.py::test_a']['phases']['call']['outcome']='skipped'
    (evidence/'test-inventory.json').write_text(json.dumps(tests))
    receipt=dict(candidate_id=m['candidate_id'],local_candidate_checks_passed=True,test_inventory_sha256=digest((evidence/'test-inventory.json').read_bytes()),
        source_inventory_sha256=source_id,profile_sha256=digest(canonical(profile)),platform=profile['platform'],code_sha256=metadata['code_sha256'],required_tests_passed=1,outside_profile_tests=0)
    if damage=='receipt_count':receipt['required_tests_passed']=2
    # Mutate after packing for the hash mismatch case.
    (evidence/'execution.json').write_text(json.dumps(receipt))
    payload=tmp_path/'release-payload.zip';pack(c,evidence,payload)
    if damage=='receipt_hash':
        with zipfile.ZipFile(payload) as z: contents={n:z.read(n) for n in z.namelist()}
        receipt['test_inventory_sha256']='0'*64;contents['evidence/execution.json']=json.dumps(receipt).encode()
        payload.unlink()
        with zipfile.ZipFile(payload,'w') as z:
            for n,d in contents.items():z.writestr(n,d)
    outer=tmp_path/'outer.zip'
    with zipfile.ZipFile(outer,'w') as z:z.write(payload,'release-payload.zip')
    artifact.update(size_in_bytes=outer.stat().st_size,digest='sha256:'+digest(outer.read_bytes()))
    if damage=='artifact_digest':artifact['digest']='sha256:'+'0'*64
    signed=signature(bindings);signed[0]['verificationResult']['statement']['subject'][0]['digest']['sha256']=digest(payload.read_bytes())
    responses={'repos/owner/project/actions/runs/10/artifacts?per_page=100':{'total_count':1,'artifacts':[artifact]},
               f'repos/owner/project/actions/runs/10/attempts/2':run,
               f'repos/owner/project/actions/runs/10/attempts/2/jobs?per_page=100':jobs,
               'repos/owner/project/actions/artifacts/30':artifact,
               f'repos/owner/project/git/trees/{e["commit"]}?recursive=1':tree(source)}
    calls=[]
    def command(gh,args,out,*a,**k):
        calls.append(args)
        if args==['attestation','verify','--help']:
            Path(out).write_text('--deny-self-hosted-runners --source-digest --signer-digest --signer-workflow --format --hostname')
        elif args==['--version']:Path(out).write_text('MOCKED CLI - NOT A CRYPTOGRAPHIC TEST')
        elif args[:2]==['attestation','verify']:
            assert '--deny-self-hosted-runners' in args and '--signer-digest' in args
            assert '--bundle' not in args and '--no-public-good' not in args
            if damage=='signature_failure':raise RuntimeError('mock signature verification failed')
            Path(out).write_text(json.dumps(signed))
        elif args[-1].endswith('/zip'):Path(out).write_bytes(outer.read_bytes())
        else:Path(out).write_text(json.dumps(responses[args[-1]]))
    monkeypatch.setattr('benchmarks.release_remote._command',command)
    monkeypatch.setattr('benchmarks.release_remote.shutil.which',lambda name:'/trusted/mock-gh')
    out=tmp_path/'report'
    if damage:
        with pytest.raises((ValueError,RuntimeError)):verify_remote(e['repo'],e['commit'],10,2,None if discover else 30,source,out)
        assert not (out/'verification.json').exists()
        assert json.loads((out/'failure.json').read_text())['independent_host_verified'] is False
    else:
        r=verify_remote(e['repo'],e['commit'],10,2,None if discover else 30,source,out)
        assert r['independent_host_verified'] and not r['industrial_release_qualified']
        assert r['candidate_id']==m['candidate_id'] and r['required_tests_passed']==1
        assert any(a[:2]==['attestation','verify'] and '--deny-self-hosted-runners' in a for a in calls)


def test_subprocess_errors_and_output_budget_do_not_expose_credentials(tmp_path):
    import sys
    from benchmarks.release_remote import _command
    with pytest.raises(RuntimeError) as exc:
        _command(sys.executable, ['-c', 'import sys;sys.stderr.write("secret-token");sys.exit(1)'], tmp_path/'failed.txt')
    assert 'secret-token' not in str(exc.value)
    with pytest.raises(RuntimeError):
        _command(sys.executable, ['-c', 'print("x"*1000000)'], tmp_path/'large.txt', limit=100)
    assert (tmp_path/'large.txt').stat().st_size<=101
    with pytest.raises(RuntimeError,match='timed out'):
        _command(sys.executable, ['-c', 'import time;time.sleep(5)'], tmp_path/'timeout.txt', timeout=.05)


def test_archived_release_workflow_actions_are_pinned_and_inactive():
    import yaml
    root=Path(__file__).resolve().parents[1]
    assert not list((root/'.github/workflows').glob('*.yml')) and not list((root/'.github/workflows').glob('*.yaml'))
    pins=json.loads((root/'requirements/release-ci-actions.json').read_text())['actions']
    for filename in ['release-candidate.yml','release-provenance.yml']:
        w=yaml.load((root/'.github/archived-workflows'/filename).read_text(),Loader=yaml.BaseLoader)
        for job in w['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:
                    name,sha=step['uses'].split('@');assert pins[name]['commit']==sha
    verifier=yaml.load((root/'.github/archived-workflows/release-provenance.yml').read_text(),Loader=yaml.BaseLoader)
    assert set(verifier['permissions'].values())=={'read'}
    assert 'permissions' not in verifier['jobs']['verify']


@pytest.mark.parametrize('damage',['missing','duplicate','partial'])
def test_automatic_artifact_discovery_is_complete_and_unambiguous(tmp_path, bindings, monkeypatch, damage):
    e,run,jobs,artifact=bindings
    entries=[] if damage=='missing' else [artifact,artifact] if damage=='duplicate' else [artifact]
    listing={'total_count':2 if damage=='partial' else len(entries),'artifacts':entries}
    def command(gh,args,out,*a,**k):
        if args==['attestation','verify','--help']:
            Path(out).write_text('--deny-self-hosted-runners --source-digest --signer-digest --signer-workflow --format --hostname')
        elif args==['--version']:Path(out).write_text('MOCK')
        elif '/artifacts?' in args[-1]:Path(out).write_text(json.dumps(listing))
        elif '/jobs?' in args[-1]:Path(out).write_text(json.dumps(jobs))
        else:Path(out).write_text(json.dumps(run))
    monkeypatch.setattr('benchmarks.release_remote._command',command)
    monkeypatch.setattr('benchmarks.release_remote.shutil.which',lambda name:'/trusted/mock-gh')
    with pytest.raises(ValueError):verify_remote(e['repo'],e['commit'],10,2,None,tmp_path/'source',tmp_path/'report')
    assert not (tmp_path/'report/verification.json').exists()


@pytest.mark.parametrize('damage',['missing','duplicate','diagnostic_misnamed'])
def test_skipped_diagnostics_do_not_hide_missing_required_build_steps(bindings,damage):
    e,run,jobs,artifact=copy.deepcopy(bindings)
    if damage=='missing':jobs['jobs'][0]['steps'].pop(0)
    if damage=='duplicate':jobs['jobs'][0]['steps'].append(jobs['jobs'][0]['steps'][0])
    if damage=='diagnostic_misnamed':jobs['jobs'][0]['steps'][-1]['name']='other skipped step'
    with pytest.raises(ValueError):metadata_contract(e,run,jobs,artifact)

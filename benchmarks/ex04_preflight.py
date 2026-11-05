"""Read-only EX-04 publication preflight. Never dispatches, commits or pushes.

This is a readiness report, never an independent-execution or attestation proof.
"""
from pathlib import Path
import argparse,json,re,subprocess
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json


def github_repository(remote):
    match=re.fullmatch(r'(?:git@github\.com:|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?',remote.strip())
    if not match or any(p in {'.','..'} for p in match[1].split('/')):raise ValueError('expected credential-free github.com repository URL')
    return match[1]


def preflight(root,runner=subprocess.run):
    root=Path(root).resolve()
    def call(*args):
        try:
            p=runner(list(args),cwd=root,capture_output=True,text=True,timeout=30,check=False)
            return p.returncode,p.stdout
        except (OSError,subprocess.TimeoutExpired):return 127,''
    status,head=call('git','rev-parse','HEAD');head=head.strip()
    if status or not re.fullmatch('[0-9a-f]{40}',head):raise ValueError('valid committed source required')
    _,branch=call('git','branch','--show-current');_,remote=call('git','remote','get-url','origin')
    try:repo=github_repository(remote)
    except ValueError:repo=None
    status,dirty=call('git','status','--porcelain=v1','--untracked-files=all','-z')
    source_clean=status==0 and not dirty
    auth,_=call('gh','auth','status','--hostname','github.com')
    capability,help_text=call('gh','attestation','verify','--help')
    flags=['--deny-self-hosted-runners','--source-digest','--signer-digest','--signer-workflow','--format','--hostname']
    supported=capability==0 and all(flag in help_text for flag in flags)
    published=False
    if auth==0 and repo:
        rc,response=call('gh','api',f'repos/{repo}/commits/{head}','--jq','.sha')
        published=rc==0 and response.strip()==head
    blockers=[]
    if not source_clean:blockers.append('working_tree_not_committed')
    if not repo:blockers.append('unsupported_or_missing_github_remote')
    if auth!=0:blockers.append('github_authentication_unavailable')
    if not supported:blockers.append('gh_attestation_verification_flags_unavailable')
    if not published:blockers.append('exact_commit_not_verified_as_published')
    return dict(schema='pge-ex04-preflight-v1',commit=head,branch=branch.strip(),repository=repo,code_sha256=package_fingerprint(),
        working_tree_clean=source_clean,github_authenticated=auth==0,attestation_cli_supported=supported,exact_commit_published=published,
        ready_to_request_remote_run=not blockers,blockers=blockers,remote_execution_verified=False,signed_provenance_verified=False,
        ex04_complete=False,mutations_performed=False,
        next_steps=['User commits and publishes the reviewed source','Run the existing release-candidate workflow_dispatch for that exact commit',
                    'Use benchmarks.release_remote to verify hosted execution, source identity and signed payload provenance'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path('.'));parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    result=preflight(args.source);write_json(args.out,result);print(json.dumps(result,indent=2))

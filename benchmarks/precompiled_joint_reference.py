"""Offline replay of hash-pinned original Model Exchange OEL/PSS artifacts.

Compilation is a separate reproducible path in oel2c_native_reference and
ex02_native_blocks. This replay never pretends to invoke a compiler. Coefficients
must match the archived passports exactly; only independently recomputed start
values may differ within 1e-10, and continuous equilibrium states are reinstalled
and checked by the original FMI master before integration.
"""
from pathlib import Path
import hashlib,json,re,zipfile
from benchmarks.dynawo_reference_setup import LOCK
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import sha256_file


class PrecompiledJoint:
    def __init__(self,cache,runtime):
        self.cache=Path(cache);self.runtime=Path(runtime)
        self.lock=json.loads(Path('requirements/joint-long-fmu.lock.json').read_text())
        self.study=StudySpec.model_validate_json((self.cache/'study.json').read_text())

    def verify(self):
        for name,h in self.lock['files'].items():
            if sha256_file(self.cache/name)!=h:raise ValueError('precompiled reference changed: '+name)
        dynawo=json.loads(LOCK.read_text());wheel=self.runtime/dynawo['fmpy']['filename']
        if sha256_file(wheel)!=dynawo['fmpy']['sha256']:raise ValueError('FMI runtime wheel differs')
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.endswith('/') or '/__pycache__/' in name:continue
                if sha256_file(self.runtime/'fmpy'/name)!=hashlib.sha256(archive.read(name)).hexdigest():raise ValueError('FMI runtime source/binary differs')
        return dict(precompiled_FMU_replay=True,compiler_invoked=False,fmpy_version=dynawo['fmpy']['version'],
                    dynawo_commit=dynawo['commit'],artifacts=self.lock['files'],provenance=self.lock['origin'],
                    coefficient_policy='exact passport match; start-value difference <=1e-10; original master resets and checks all equilibrium states')

    def oel(self,p,input0):
        old=next(q for q in self.study.coupled.oel2c_limiters if q.generator_id==p.generator_id)
        if old.model_dump(mode='json')!=p.model_dump(mode='json'):raise ValueError('precompiled OEL passport differs')
        i=self.study.coupled.oel2c_limiters.index(old);folder=self.cache/('block-'+str(i))
        text=(folder/'Reference.mo').read_text();start=float(re.search(r'fieldCurrent\(start=([^)]*)\)',text)[1])
        if abs(start-input0)>1e-10:raise ValueError('precompiled OEL initial current differs')
        return folder/'Reference.fmu'

    def pss(self,gid,p,voltage0):
        old=next(q for q in self.study.coupled.machines if q.generator_id==gid)
        if old.stabilizer.model_dump(mode='json')!=p.model_dump(mode='json'):raise ValueError('precompiled PSS passport differs')
        ids=[q.generator_id for q in self.study.coupled.machines if q.stabilizer]
        folder=self.cache/('pss-'+str(ids.index(gid)))
        text=(folder/'Reference.mo').read_text();start=float(re.search(r'voltage\(start=([^)]*)\)',text)[1])
        if abs(start-voltage0)>1e-10:raise ValueError('precompiled PSS initial voltage differs')
        return folder/'AuxReference.fmu'


def prepare(cache, runtime, archive, wheel):
    """Restore a local cache from the retained qualification and a local wheel.

    No downloads and no execution of archive content; all retained bytes are
    checked against the committed manifest before copying or extraction.
    """
    import shutil
    cache,runtime,archive,wheel=map(Path,(cache,runtime,archive,wheel))
    lock=json.loads(Path('requirements/joint-long-fmu.lock.json').read_text())
    dynawo=json.loads(LOCK.read_text())
    if sha256_file(wheel)!=dynawo['fmpy']['sha256']:raise ValueError('FMPy wheel differs')
    staged={}
    for name,h in lock['files'].items():
        part=Path(name)
        if part.is_absolute() or '..' in part.parts:raise ValueError('unsafe cache manifest path')
        candidates=[archive/part,archive.parent/part]
        if name.startswith('Dynawo-'):candidates.append(Path('data/external/dynawo')/name.removeprefix('Dynawo-').removesuffix('.txt'))
        source=next((p for p in candidates if p.is_file() and sha256_file(p)==h),None)
        if source is None:raise ValueError('missing or changed original artifact: '+name)
        staged[part]=source
    if cache.exists() or runtime.exists():raise ValueError('fresh cache and runtime destinations required')
    with zipfile.ZipFile(wheel) as zipped:
        for name in zipped.namelist():
            part=Path(name)
            if part.is_absolute() or '..' in part.parts:raise ValueError('unsafe runtime wheel path')
        for part,source in staged.items():
            target=cache/part;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        runtime.mkdir(parents=True);shutil.copyfile(wheel,runtime/dynawo['fmpy']['filename'])
        zipped.extractall(runtime/'fmpy')
    return PrecompiledJoint(cache,runtime).verify()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Verify or reconstruct an offline native reference cache')
    parser.add_argument('--cache',required=True);parser.add_argument('--runtime',required=True)
    parser.add_argument('--archive');parser.add_argument('--wheel');args=parser.parse_args()
    if bool(args.archive)!=bool(args.wheel):parser.error('--archive and --wheel must be supplied together')
    result=prepare(args.cache,args.runtime,args.archive,args.wheel) if args.archive else PrecompiledJoint(args.cache,args.runtime).verify()
    print(json.dumps(result,indent=2))

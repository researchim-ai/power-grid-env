"""Pytest plugin: collect the whole suite, execute an explicit release subset.

pytest -p benchmarks.release_inventory --release-profile PROFILE --release-inventory OUT
No skipped/xfail/deselected required test can qualify this execution.
"""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from benchmarks.release_verify import canonical, digest, strict_json


def pytest_addoption(parser):
    group = parser.getgroup('release-inventory')
    group.addoption('--release-profile', type=Path)
    group.addoption('--release-inventory', type=Path)


def pytest_configure(config):
    if bool(config.getoption('--release-profile')) != bool(config.getoption('--release-inventory')):
        raise pytest.UsageError('release profile and inventory must be supplied together')
    if config.getoption('--release-profile'):
        config._pge_inventory = Inventory(config)
        config.pluginmanager.register(config._pge_inventory, 'pge-release-recorder')


class Inventory:
    def __init__(self, config):
        self.config = config
        if config.args != ['tests'] or config.getoption('ignore') or config.getoption('ignore_glob') or config.getoption('deselect'):
            raise pytest.UsageError('release inventory requires full collection: pytest tests, no ignore/deselect')
        from benchmarks.release_candidate import source_hashes
        self.source_before = source_hashes(Path(config.rootpath))
        self.profile = strict_json(config.getoption('--release-profile'))
        self.files = set(self.profile['required_test_files'])
        if not self.files or any(not x.startswith('tests/test_') or not x.endswith('.py') for x in self.files):
            raise pytest.UsageError('nonempty explicit required test file list needed')
        self.collected = {}; self.errors = []; self.selected = set(); self.deselected = []

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        for item in items:
            if item.nodeid in self.collected:
                self.errors.append('duplicate nodeid: ' + item.nodeid)
            self.collected[item.nodeid] = {'status': 'outside_profile', 'reason': 'not in declared release test files', 'phases': {}}
        selected = [i for i in items if i.nodeid.split('::')[0] in self.files]
        rejected = [i for i in items if i.nodeid.split('::')[0] not in self.files]
        self.selected = {i.nodeid for i in selected}
        for nodeid in self.selected:
            self.collected[nodeid].update(status='not_run', reason='required test did not finish')
        found = {i.nodeid.split('::')[0] for i in selected}
        if found != self.files:
            self.errors.append('missing required test files: ' + repr(sorted(self.files - found)))
        config.hook.pytest_deselected(items=rejected)
        items[:] = selected

    def pytest_deselected(self, items):
        # Also captures -k/-m filtering by other plugins: required tests remain not_run.
        self.deselected.extend(i.nodeid for i in items)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.errors.append('collection ' + report.outcome + ': ' + report.nodeid + ': ' + str(report.longrepr))

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in self.collected:
            self.errors.append('uncollected result: ' + report.nodeid)
            return
        self.collected[report.nodeid]['phases'][report.when] = {
            'outcome': report.outcome, 'duration_s': report.duration,
            'xfail': getattr(report, 'wasxfail', None),
            'detail': str(report.longrepr) if report.failed or report.skipped else ''}

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        for nodeid in self.selected:
            entry = self.collected[nodeid]; phases = entry['phases']
            passed = set(phases) == {'setup', 'call', 'teardown'} and all(
                p['outcome'] == 'passed' and p['xfail'] is None for p in phases.values())
            entry.update(status='passed' if passed else 'failed_or_incomplete', reason='' if passed else 'required setup/call/teardown did not all pass without xfail')
        from benchmarks.release_candidate import source_hashes
        if source_hashes(Path(self.config.rootpath)) != self.source_before:
            self.errors.append('source changed during test execution')
        passed = bool(self.selected) and not self.errors and int(exitstatus) == 0 and all(self.collected[n]['status'] == 'passed' for n in self.selected)
        result = {'schema': 'pge-test-inventory-v1', 'profile_sha256': digest(canonical(self.profile)),
                  'source_inventory_sha256': digest(canonical(self.source_before)),
                  'passed': passed, 'pytest_exitstatus': int(exitstatus), 'collection_errors': self.errors,
                  'collected_count': len(self.collected), 'required_count': len(self.selected),
                  'outside_profile_count': len(self.collected) - len(self.selected),
                  'all_project_tests_executed': bool(self.collected) and len(self.selected) == len(self.collected),
                  'tests': dict(sorted(self.collected.items()))}
        out = self.config.getoption('--release-inventory'); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        if not passed and int(session.exitstatus) == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

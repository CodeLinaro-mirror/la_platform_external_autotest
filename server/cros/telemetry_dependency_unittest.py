#!/usr/bin/python3
# Lint as: python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for server/cros/telemetry_dependency.py."""

import ast
import collections
import os
import shutil
import tempfile
import unittest
from unittest import mock
import warnings

import common
from autotest_lib.server.cros import telemetry_dependency

# Minimal control file body. NAME and TIME are required by the parser.
_CONTROL = """
NAME = "%s"
AUTHOR = "someone"
TIME = "SHORT"
TEST_TYPE = "client"
DOC = "doc"
%s
job.run_test('%s')
"""


def _control(name, extra=''):
    return _CONTROL % (name, extra, name)


class DeclarationTestBase(unittest.TestCase):
    """Fixture providing a synthetic autotest tree. Holds no tests itself."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='telemetry_dependency_test_')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        patcher = mock.patch.object(telemetry_dependency, '_AUTOTEST_ROOT',
                                    self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        telemetry_dependency._cache.clear()
        self.addCleanup(telemetry_dependency._cache.clear)

    def _write(self, relative_path, source):
        """Writes a file into the synthetic tree."""
        path = os.path.join(self.root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(source)
        return path

    def _write_control(self, test_name, source, filename='control'):
        return self._write(
                os.path.join('client', 'site_tests', test_name, filename),
                source)

    def _write_module(self, test_name, source, subdir='site_tests'):
        base = os.path.basename(test_name)
        return self._write(
                os.path.join('client', subdir, test_name, '%s.py' % base),
                source)

    def _needs(self, name):
        return telemetry_dependency.client_test_needs_telemetry(name)


class ControlFileDeclarationTestCase(DeclarationTestBase):
    """Tests reading REQUIRE_TELEMETRY from control files."""

    def test_unknown_test(self):
        self.assertFalse(self._needs('no_such_test'))

    def test_not_declared(self):
        self._write_control('plain_Test', _control('plain_Test'))
        self.assertFalse(self._needs('plain_Test'))

    def test_declared_true(self):
        self._write_control('login_Test',
                            _control('login_Test', 'REQUIRE_TELEMETRY = True'))
        self.assertTrue(self._needs('login_Test'))

    def test_declared_false(self):
        self._write_control('off_Test',
                            _control('off_Test', 'REQUIRE_TELEMETRY = False'))
        self.assertFalse(self._needs('off_Test'))

    def test_any_variant_counts(self):
        """power_LoadTest has 24 control files; annotating one is enough."""
        self._write_control('multi_Test', _control('multi_Test'))
        self._write_control('multi_Test',
                            _control('multi_Test', 'REQUIRE_TELEMETRY = True'),
                            filename='control.fast')
        self.assertTrue(self._needs('multi_Test'))

    def test_unparseable_control_file_is_skipped(self):
        self._write_control('broken_Test', 'this is not python (((')
        self._write_control('broken_Test',
                            _control('broken_Test',
                                     'REQUIRE_TELEMETRY = True'),
                            filename='control.ok')
        self.assertTrue(self._needs('broken_Test'))

    def test_dependencies_field_is_not_used(self):
        """DEPENDENCIES is a DUT label matcher and must not trigger a deploy."""
        self._write_control('dep_Test',
                            _control('dep_Test', "DEPENDENCIES = 'telemetry'"))
        self.assertFalse(self._needs('dep_Test'))


class ModuleDeclarationTestCase(DeclarationTestBase):
    """Tests reading REQUIRE_TELEMETRY from the test module."""

    def test_declared_in_module(self):
        """Covers tests with no control file, e.g. login_LoginSuccess."""
        self._write_module('login_LoginSuccess',
                           'REQUIRE_TELEMETRY = True\n\nclass foo: pass\n')
        self.assertTrue(self._needs('login_LoginSuccess'))

    def test_not_declared_in_module(self):
        self._write_module('plain_Test', 'import os\n')
        self.assertFalse(self._needs('plain_Test'))

    def test_module_declared_false(self):
        self._write_module('off_Test', 'REQUIRE_TELEMETRY = False\n')
        self.assertFalse(self._needs('off_Test'))

    def test_later_assignment_overrides_earlier(self):
        """Last module-level assignment wins, matching control_data.py."""
        self._write_module(
                'override_True',
                'REQUIRE_TELEMETRY = False\nREQUIRE_TELEMETRY = True\n')
        self.assertTrue(self._needs('override_True'))

        self._write_module(
                'override_False',
                'REQUIRE_TELEMETRY = True\nREQUIRE_TELEMETRY = False\n')
        self.assertFalse(self._needs('override_False'))

    def test_type_annotated_assignment(self):
        """Supports ast.AnnAssign (REQUIRE_TELEMETRY: bool = True)."""
        self._write_module('typed_True', 'REQUIRE_TELEMETRY: bool = True\n')
        self.assertTrue(self._needs('typed_True'))

        self._write_module('typed_False', 'REQUIRE_TELEMETRY: bool = False\n')
        self.assertFalse(self._needs('typed_False'))

    def test_nested_assignment_is_ignored(self):
        """Only module-level declarations count."""
        self._write_module('nested_Test',
                           'class Thing:\n    REQUIRE_TELEMETRY = True\n')
        self.assertFalse(self._needs('nested_Test'))

    def test_non_constant_assignment_is_ignored(self):
        self._write_module('dynamic_Test',
                           'import os\nREQUIRE_TELEMETRY = os.environ\n')
        self.assertFalse(self._needs('dynamic_Test'))

    def test_syntax_error_is_skipped(self):
        self._write_module('broken_Test', 'def (((\n')
        self.assertFalse(self._needs('broken_Test'))

    def test_control_file_and_module_both_consulted(self):
        self._write_control('mixed_Test', _control('mixed_Test'))
        self._write_module('mixed_Test', 'REQUIRE_TELEMETRY = True\n')
        self.assertTrue(self._needs('mixed_Test'))

    def test_importing_telemetry_alone_is_not_enough(self):
        """The scan is gone: importing telemetry no longer implies a deploy."""
        self._write_module('implicit_Test',
                           'from telemetry.core import exceptions\n')
        self.assertFalse(self._needs('implicit_Test'))


class TestDirResolutionTestCase(DeclarationTestBase):
    """Tests directory lookup, test-name normalization, and path safety."""

    def test_falls_back_to_client_tests_dir(self):
        """Checks client/tests when client/site_tests has no match."""
        self._write_module('legacy_Test',
                           'REQUIRE_TELEMETRY = True\n',
                           subdir='tests')
        self.assertTrue(self._needs('legacy_Test'))

    def test_normalizes_colon_separator(self):
        """client/common_lib/test.py converts ':' in test names into '/'."""
        self._write_module(os.path.join('group', 'sub_Test'),
                           'REQUIRE_TELEMETRY = True\n')
        self.assertTrue(self._needs('group:sub_Test'))

    def test_strips_package_tag_suffix(self):
        """job.stage_control_file strips '.<tag>' (e.g. 'camera_HAL3.jea')."""
        self._write_module('camera_HAL3', 'REQUIRE_TELEMETRY = True\n')
        self.assertTrue(self._needs('camera_HAL3.jea'))

    def test_rejects_path_traversal_and_absolute_paths(self):
        self._write('outside/secret.py', 'REQUIRE_TELEMETRY = True\n')
        self.assertFalse(self._needs('../../outside'))
        self.assertFalse(self._needs('/etc'))
        self.assertFalse(self._needs(''))


class CacheTestCase(DeclarationTestBase):
    """Tests memoization."""

    def test_result_is_cached(self):
        path = self._write_module('cached_Test', 'import os\n')
        self.assertFalse(self._needs('cached_Test'))
        with open(path, 'w') as f:
            f.write('REQUIRE_TELEMETRY = True\n')
        self.assertFalse(self._needs('cached_Test'))


class ControlExecutionTestCase(DeclarationTestBase):
    """Tests control_file_needs_telemetry and ensure_for_client_control.

    Standalone client tests (client_wrapper, client_trampoline,
    WrapperTestRunner) bypass Autotest.run_timed_test() and go straight through
    Autotest.run() -> _do_run(), so the check must work from the control file
    being sent to the DUT.
    """

    def test_control_file_declaring_require_telemetry_directly(self):
        control_path = self._write(
                'tmp/control.autoserv',
                _control('some_UnregisteredName', 'REQUIRE_TELEMETRY = True'))
        self.assertTrue(
                telemetry_dependency.control_file_needs_telemetry(
                        control_path))

    def test_control_file_invoking_declaring_test_via_run_test(self):
        """Covers run_timed_test and unannotated variant control files."""
        self._write_module('login_LoginSuccess', 'REQUIRE_TELEMETRY = True\n')
        control_path = self._write('tmp/control',
                                   "job.run_test('login_LoginSuccess')\n")
        self.assertTrue(
                telemetry_dependency.control_file_needs_telemetry(
                        control_path))

    def test_control_file_string_false_does_not_fall_through(self):
        """When control_data parses a valid control file, its answer is final."""
        control = _control('plain_Test', 'REQUIRE_TELEMETRY = False')
        with mock.patch.object(telemetry_dependency,
                               '_declared_in_module_tree',
                               return_value=True) as fallback:
            self.assertFalse(
                    telemetry_dependency._declared_in_control_string(control))
            fallback.assert_not_called()

    def test_control_file_resolves_test_from_name_variable(self):
        """Covers control files that call job.run_test(url=NAME.split('.')[0])."""
        self._write_module('hardware_StorageFio', 'REQUIRE_TELEMETRY = True\n')
        control = ('NAME = "hardware_StorageFio.bvt"\n'
                   'AUTHOR = "someone"\n'
                   'TIME = "SHORT"\n'
                   'TEST_TYPE = "client"\n'
                   'DOC = "doc"\n'
                   "job.run_test(url=NAME.split('.')[0])\n")
        self.assertTrue(
                telemetry_dependency.control_string_needs_telemetry(control))

    def test_control_file_invoking_declaring_test_via_keyword_or_tag(self):
        self._write_module('power_LoadTest', 'REQUIRE_TELEMETRY = True\n')
        self.assertTrue(
                telemetry_dependency.control_string_needs_telemetry(
                        "job.run_test(url='power_LoadTest.fast', tag='1')\n"))
        self.assertTrue(
                telemetry_dependency.control_string_needs_telemetry(
                        "job.run_test(non_literal, url='power_LoadTest')\n"))

    def test_client_trampoline_control_file(self):
        """Covers server_job with use_client_trampoline=True."""
        self._write_module('power_LoadTest', 'REQUIRE_TELEMETRY = True\n')
        trampoline = (
                "trampoline_testname = 'power_LoadTest.fast'\n"
                "def _client_trampoline():\n"
                "    path = job.stage_control_file(trampoline_testname)\n"
                "_client_trampoline()\n")
        self.assertTrue(
                telemetry_dependency.control_string_needs_telemetry(
                        trampoline))

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut',
                       return_value=True)
    def test_ensure_for_client_control_deploys_when_needed(self, ensure):
        self._write_module('login_LoginSuccess', 'REQUIRE_TELEMETRY = True\n')
        control_path = self._write('tmp/control',
                                   "job.run_test('login_LoginSuccess')\n")
        host = mock.Mock()
        self.assertTrue(
                telemetry_dependency.ensure_for_client_control(
                        host, control_path))
        ensure.assert_called_once_with(host, minimal_only=True)

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut')
    def test_ensure_for_client_control_skips_when_not_needed(self, ensure):
        self._write_module('plain_Test', 'import os\n')
        control_path = self._write('tmp/control',
                                   "job.run_test('plain_Test')\n")
        self.assertFalse(
                telemetry_dependency.ensure_for_client_control(
                        mock.Mock(), control_path))
        ensure.assert_not_called()

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut')
    @mock.patch.object(telemetry_dependency,
                       'control_string_needs_telemetry',
                       side_effect=RuntimeError('unexpected ast bug'))
    def test_ensure_for_client_control_string_swallows_static_check_error(
            self, _needs, ensure):
        self.assertFalse(
                telemetry_dependency.ensure_for_client_control_string(
                        mock.Mock(), 'job.run_test("x")\n'))
        ensure.assert_not_called()

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut',
                       side_effect=telemetry_dependency.telemetry_deploy.
                       TelemetryDeployError('disk full'))
    def test_ensure_for_client_control_string_propagates_deploy_error(
            self, _ensure):
        self._write_module('login_LoginSuccess', 'REQUIRE_TELEMETRY = True\n')
        with self.assertRaises(
                telemetry_dependency.telemetry_deploy.TelemetryDeployError):
            telemetry_dependency.ensure_for_client_control_string(
                    mock.Mock(), "job.run_test('login_LoginSuccess')\n")


class EnsureForClientTestCase(unittest.TestCase):
    """Tests the per-test deployment entry point."""

    def setUp(self):
        telemetry_dependency._cache.clear()
        self.addCleanup(telemetry_dependency._cache.clear)

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut')
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       return_value=False)
    def test_skips_when_not_declared(self, _needs, ensure):
        self.assertFalse(
                telemetry_dependency.ensure_for_client_test(
                        mock.Mock(), 'plain_Test'))
        ensure.assert_not_called()

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut',
                       return_value=True)
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       return_value=True)
    def test_deploys_when_declared(self, _needs, ensure):
        host = mock.Mock()
        self.assertTrue(
                telemetry_dependency.ensure_for_client_test(
                        host, 'login_Test'))
        ensure.assert_called_once_with(host, minimal_only=True)

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut')
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       side_effect=RuntimeError('unexpected ast bug'))
    def test_swallows_static_check_error(self, _needs, ensure):
        self.assertFalse(
                telemetry_dependency.ensure_for_client_test(
                        mock.Mock(), 'plain_Test'))
        ensure.assert_not_called()

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut',
                       side_effect=telemetry_dependency.telemetry_deploy.
                       TelemetryDeployError('disk full'))
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       return_value=True)
    def test_propagates_deploy_error(self, _needs, _ensure):
        with self.assertRaises(
                telemetry_dependency.telemetry_deploy.TelemetryDeployError):
            telemetry_dependency.ensure_for_client_test(
                    mock.Mock(), 'login_Test')

    @mock.patch.object(
            telemetry_dependency.telemetry_deploy,
            'ensure_telemetry_on_dut',
            side_effect=[
                    telemetry_dependency.telemetry_deploy.TelemetryDeployError(
                            'no build'),
                    True,
            ])
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       return_value=True)
    def test_falls_back_to_release_builder_path_in_cft(self, _needs, ensure):
        """Covers CFT (tauto), where --host_labels omits cros-version:."""
        host = mock.Mock()
        host.host_info_store.get.return_value = mock.Mock(build=None)
        host.get_release_builder_path.return_value = (
                'octopus-release/R156-16825.0.0')
        self.assertTrue(
                telemetry_dependency.ensure_for_client_test(
                        host, 'login_Test'))
        self.assertEqual(ensure.call_args_list, [
                mock.call(host, minimal_only=True),
                mock.call(host,
                          build='octopus-release/R156-16825.0.0',
                          minimal_only=True),
        ])

    @mock.patch.object(telemetry_dependency.telemetry_deploy,
                       'ensure_telemetry_on_dut',
                       side_effect=telemetry_dependency.telemetry_deploy.
                       TelemetryDeployError('no build'))
    @mock.patch.object(telemetry_dependency,
                       'client_test_needs_telemetry',
                       return_value=True)
    def test_raises_when_both_host_info_and_release_builder_path_fail(
            self, _needs, ensure):
        host = mock.Mock()
        host.host_info_store.get.return_value = mock.Mock(build=None)
        host.get_release_builder_path.side_effect = RuntimeError('ssh failed')
        with self.assertRaises(
                telemetry_dependency.telemetry_deploy.TelemetryDeployError):
            telemetry_dependency.ensure_for_client_test(host, 'login_Test')
        ensure.assert_called_once_with(host, minimal_only=True)


class ControlDataIntegrationTestCase(unittest.TestCase):
    """Tests that control_data actually parses the new variable."""

    def test_parses_require_telemetry(self):
        from autotest_lib.client.common_lib import control_data
        control = control_data.parse_control_string(
                _control('x', 'REQUIRE_TELEMETRY = True'))
        self.assertTrue(control.require_telemetry)

    def test_defaults_to_false(self):
        from autotest_lib.client.common_lib import control_data
        control = control_data.parse_control_string(_control('x'))
        self.assertFalse(control.require_telemetry)


# Catapult top-level packages shipped under /usr/local/telemetry: telemetry.pth
# exposes `telemetry`, whose __init__.py adds these sibling Catapult packages
# to sys.path at import time.
_TELEMETRY_IMPORT_ROOTS = frozenset(
        ('telemetry', 'py_utils', 'devil', 'catapult_base', 'tracing', 'typ'))


def _resolve_file_or_package(base_path):
    """Resolves a module path stem to `<stem>.py` or `<stem>/__init__.py`.

    Args:
        base_path: Path to a module or package without a `.py` suffix.

    Returns:
        Matching `.py` or package `__init__.py` path, or None if absent.
    """
    for candidate in (
            base_path + '.py',
            os.path.join(base_path, '__init__.py'),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_autotest_module(autotest_root, dotted):
    """Resolves an `autotest_lib.*` module name to a file path if present.

    Args:
        autotest_root: Root directory of the autotest checkout.
        dotted: Fully qualified dotted module name.

    Returns:
        Resolved `.py` or package `__init__.py` file path, or None.
    """
    if not dotted.startswith('autotest_lib.'):
        return None
    rel = dotted[len('autotest_lib.'):].replace('.', os.sep)
    return _resolve_file_or_package(os.path.join(autotest_root, rel))


_AuditResult = collections.namedtuple(
        '_AuditResult', ('requiring', 'missing', 'uses_telemetry'))


def _parse_module_edges(autotest_root, py_path):
    """Extracts direct Telemetry root import and local deps from `py_path`.

    Args:
        autotest_root: Root directory of the autotest checkout.
        py_path: File path to parse as Python source (`.py` or `control*`).

    Returns:
        Tuple `(direct_root, internal_dep_paths)` of `(Optional[str], list)`,
        where `direct_root` is the first matched root module name in
        `_TELEMETRY_IMPORT_ROOTS` (so top-level name collisions like `tracing`
        or `typ` are visible at the tail of reported import chains).
    """
    try:
        with open(py_path, 'r', encoding='utf-8', errors='replace') as f:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', SyntaxWarning)
                tree = ast.parse(f.read(), filename=py_path)
    except (OSError, SyntaxError, ValueError, TypeError):
        # Skip legacy Python 2 modules (e.g. client/tests/netpipe/netpipe.py,
        # client/deps/lansim/src/py/simulator.py), files containing NUL bytes,
        # and dangling symlinks under client/.
        return None, []

    direct_root = None
    deps = []
    cur_dir = os.path.dirname(py_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split('.')[0]
                if (direct_root is None
                            and root_name in _TELEMETRY_IMPORT_ROOTS):
                    direct_root = root_name
                target = _resolve_autotest_module(autotest_root, alias.name)
                if target:
                    deps.append(target)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            root_name = mod.split('.')[0]
            if (direct_root is None and node.level == 0
                        and root_name in _TELEMETRY_IMPORT_ROOTS):
                direct_root = root_name
            if node.level > 0:
                base = cur_dir
                for _ in range(node.level - 1):
                    base = os.path.dirname(base)
                rel_base = (os.path.join(base, mod.replace('.', os.sep))
                            if mod else base)
                rel_targets = ([rel_base] if mod else []) + [
                        os.path.join(rel_base, alias.name)
                        for alias in node.names
                ]
                for target_base in rel_targets:
                    candidate = _resolve_file_or_package(target_base)
                    if candidate:
                        deps.append(candidate)
            elif mod:
                for dotted in [mod] + [
                        '%s.%s' % (mod, alias.name) for alias in node.names
                ]:
                    target = _resolve_autotest_module(autotest_root, dotted)
                    if target:
                        deps.append(target)
    return direct_root, deps


def _format_import_chain(root, start_path, via, direct_roots):
    """Formats the import chain from `start_path` to its Telemetry root import.

    Args:
        root: Root directory of the audited autotest checkout.
        start_path: Absolute path of the entry file inside the client test.
        via: Mapping from caller path to its next hop toward Telemetry.
        direct_roots: Mapping from file path to its direct Telemetry root name.

    Returns:
        Arrow-separated relative path chain ending in the Telemetry root name.
    """
    chain = []
    cur = start_path
    while cur is not None:
        chain.append(os.path.relpath(cur, root))
        if cur in direct_roots:
            chain.append(direct_roots[cur])
            break
        cur = via.get(cur)
    return ' -> '.join(chain)


def _audit_client_tree(root):
    """Finds client tests under `root` importing Telemetry and missing decls.

    Args:
        root: Root directory of the autotest tree to audit.

    Returns:
        `_AuditResult(requiring, missing, uses_telemetry)` where `requiring` is
        the sorted list of client test names importing Telemetry, `missing` maps
        each undeclared test name to its formatted import chain, and
        `uses_telemetry` is the set of root-relative file paths reaching
        Telemetry.
    """
    client_root = os.path.join(root, 'client')
    direct_roots = {}
    reverse_deps = {}

    for dirpath, dirnames, filenames in os.walk(client_root):
        dirnames.sort()
        for fname in sorted(filenames):
            if not (fname.endswith('.py') or fname == 'control'
                    or fname.startswith('control.')):
                continue
            path = os.path.join(dirpath, fname)
            direct_root, deps = _parse_module_edges(root, path)
            if direct_root is not None:
                direct_roots[path] = direct_root
            for dep in deps:
                reverse_deps.setdefault(dep, []).append(path)

    # Walk reverse dependencies breadth-first while recording parent pointers
    # so failures report the shortest import chain to Telemetry.
    uses_telemetry = set(direct_roots)
    via = {}
    queue = collections.deque(sorted(uses_telemetry))
    while queue:
        current = queue.popleft()
        for caller in reverse_deps.get(current, ()):
            if caller not in uses_telemetry:
                uses_telemetry.add(caller)
                via[caller] = current
                queue.append(caller)

    requiring = []
    missing = {}
    # pylint: disable=protected-access
    client_test_dirs = telemetry_dependency._CLIENT_TEST_DIRS
    # pylint: enable=protected-access
    for rel_dir in client_test_dirs:
        tests_dir = os.path.join(root, rel_dir)
        if not os.path.isdir(tests_dir):
            continue
        for entry in sorted(os.listdir(tests_dir)):
            test_dir = os.path.join(tests_dir, entry)
            if not os.path.isdir(test_dir):
                continue
            prefix = test_dir + os.sep
            matching = sorted(path for path in uses_telemetry
                              if path.startswith(prefix))
            if matching:
                requiring.append(entry)
                if not telemetry_dependency.client_test_needs_telemetry(entry):
                    missing[entry] = _format_import_chain(
                            root, matching[0], via, direct_roots)

    rel_uses = {os.path.relpath(path, root) for path in uses_telemetry}
    return _AuditResult(requiring=requiring,
                        missing=missing,
                        uses_telemetry=rel_uses)


class TreeAuditSyntheticTestCase(DeclarationTestBase):
    """Unit tests for the AST import graph helpers on synthetic trees."""

    def test_resolve_autotest_module_handles_modules_packages_and_external(
            self):
        mod_path = self._write('client/common_lib/helper.py', 'x = 1\n')
        pkg_init = self._write('client/cros/pkg/__init__.py', 'x = 1\n')

        self.assertEqual(
                _resolve_autotest_module(
                        self.root, 'autotest_lib.client.common_lib.helper'),
                mod_path)
        self.assertEqual(
                _resolve_autotest_module(self.root,
                                         'autotest_lib.client.cros.pkg'),
                pkg_init)
        self.assertIsNone(
                _resolve_autotest_module(self.root,
                                         'autotest_lib.client.missing'))
        self.assertIsNone(_resolve_autotest_module(self.root, 'os.path'))

    def test_parse_module_edges_handles_errors_warnings_and_stdlib(self):
        broken_py = self._write('client/broken.py', 'def (((\n')
        nul_py = self._write('client/nul.py', 'x = "\x00"\n')
        regex_py = self._write('client/legacy_escape.py',
                               'import telemetry\nPAT = "\\d+"\n')
        missing_py = os.path.join(self.root, 'client', 'missing.py')

        self.assertEqual(_parse_module_edges(self.root, broken_py), (None, []))
        self.assertEqual(_parse_module_edges(self.root, nul_py), (None, []))
        self.assertEqual(_parse_module_edges(self.root, missing_py),
                         (None, []))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', SyntaxWarning)
            self.assertEqual(_parse_module_edges(self.root, regex_py),
                             ('telemetry', []))
            self.assertEqual(caught, [])

    def test_audit_detects_transitive_undeclared_imports_and_clears_when_set(
            self):
        # 1. Transitive via autotest_lib.client.* module -> package __init__.py
        self._write('client/cros/net/__init__.py', 'import telemetry\n')
        self._write('client/cros/wrapper.py',
                    'from autotest_lib.client.cros import net\n')
        self._write_module('undeclared_ModTest',
                           'from autotest_lib.client.cros import wrapper\n')

        # 2. Transitive via `from . import bridge` and `from .. import subpkg`
        self._write('client/cros/rel/subpkg/__init__.py',
                    'from py_utils import cloud_storage\n')
        self._write('client/cros/rel/inner/bridge.py',
                    'from .. import subpkg\n')
        self._write('client/cros/rel/inner/entry.py', 'from . import bridge\n')
        self._write_module(
                'undeclared_RelTest',
                'from autotest_lib.client.cros.rel.inner import entry\n',
                subdir='tests')

        # 3. Direct/transitive import inside a `control.*` file only
        self._write_control(
                'undeclared_CtrlTest',
                _control('undeclared_CtrlTest',
                         'from autotest_lib.client.cros import wrapper'),
                filename='control.variant')

        # Unrelated non-Telemetry client test should not appear in `requiring`.
        self._write_module('plain_Test', 'import json\n')

        result = _audit_client_tree(self.root)
        self.assertEqual(result.requiring, [
                'undeclared_CtrlTest',
                'undeclared_ModTest',
                'undeclared_RelTest',
        ])
        self.assertEqual(
                result.missing, {
                        'undeclared_CtrlTest':
                        ('client/site_tests/undeclared_CtrlTest/control.variant'
                         ' -> client/cros/wrapper.py'
                         ' -> client/cros/net/__init__.py -> telemetry'),
                        'undeclared_ModTest':
                        ('client/site_tests/undeclared_ModTest/'
                         'undeclared_ModTest.py -> client/cros/wrapper.py'
                         ' -> client/cros/net/__init__.py -> telemetry'),
                        'undeclared_RelTest':
                        ('client/tests/undeclared_RelTest/undeclared_RelTest.py'
                         ' -> client/cros/rel/inner/entry.py'
                         ' -> client/cros/rel/inner/bridge.py'
                         ' -> client/cros/rel/subpkg/__init__.py -> py_utils'),
                })
        self.assertIn('client/cros/wrapper.py', result.uses_telemetry)

        # Declaring REQUIRE_TELEMETRY = True on each clears `missing`.
        telemetry_dependency._cache.clear()  # pylint: disable=protected-access
        self._write_control(
                'undeclared_ModTest',
                _control('undeclared_ModTest', 'REQUIRE_TELEMETRY = True'))
        self._write_module(
                'undeclared_RelTest',
                ('REQUIRE_TELEMETRY = True\n'
                 'from autotest_lib.client.cros.rel.inner import entry\n'),
                subdir='tests')
        self._write_control(
                'undeclared_CtrlTest',
                _control('undeclared_CtrlTest', 'REQUIRE_TELEMETRY = True'))

        result_after = _audit_client_tree(self.root)
        self.assertEqual(result_after.requiring, [
                'undeclared_CtrlTest',
                'undeclared_ModTest',
                'undeclared_RelTest',
        ])
        self.assertEqual(result_after.missing, {})


class TreeAuditTestCase(unittest.TestCase):
    """Verifies every client test importing Telemetry declares it."""

    def setUp(self):
        # pylint: disable=protected-access
        telemetry_dependency._cache.clear()
        self.addCleanup(telemetry_dependency._cache.clear)
        # pylint: enable=protected-access

    def test_all_telemetry_importing_client_tests_declare_it(self):
        """Prevents client tests from silently missing REQUIRE_TELEMETRY."""
        # pylint: disable=protected-access
        result = _audit_client_tree(telemetry_dependency._AUTOTEST_ROOT)
        # pylint: enable=protected-access
        # Structural non-vacuity guard: confirm the dominant Telemetry helper
        # and at least one declaring test are discovered without hardcoding a
        # test count that breaks as legacy client tests migrate or get removed.
        self.assertIn('client/common_lib/cros/chrome.py',
                      result.uses_telemetry)
        self.assertTrue(result.requiring)
        self.assertEqual(
                result.missing, {},
                'Client tests importing Telemetry without declaring '
                'REQUIRE_TELEMETRY = True:\n  ' +
                '\n  '.join('%s: %s' % (name, chain)
                            for name, chain in sorted(result.missing.items())))


if __name__ == '__main__':
    unittest.main()

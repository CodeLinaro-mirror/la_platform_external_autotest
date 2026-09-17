#!/usr/bin/python3
# Lint as: python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for server/cros/telemetry_dependency.py."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

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
        ensure.assert_called_once_with(host)

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
        ensure.assert_called_once_with(host)

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
                mock.call(host),
                mock.call(host, build='octopus-release/R156-16825.0.0'),
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
        ensure.assert_called_once_with(host)


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


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/python3
# Lint as: python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for server/cros/telemetry_deploy.py."""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

import common
from autotest_lib.client.common_lib import global_config
from autotest_lib.client.common_lib import packages
from autotest_lib.server.cros import telemetry_deploy


class FakeCmdResult(object):
    """Stand-in for autotest's CmdResult."""

    def __init__(self, exit_status=0, stdout='', stderr=''):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


# df output with plenty of room, which is the uninteresting default.
_ROOMY_DF = (
        'Filesystem     1024-blocks    Used Available Capacity Mounted on\n'
        '/dev/sda1         20000000 1000000  19000000       6% '
        '/mnt/stateful_partition\n')

# du output for an existing ~1.2 GiB Telemetry tree.
_EXISTING_TREE_DU = '1258291\t/usr/local/telemetry\n'

# du output when the tree is not there. du exits non-zero in this case.
_NO_TREE_DU = 'du: cannot access \'/usr/local/telemetry\': No such file\n'


class FakeHost(object):
    """Minimal host double that records commands and scripts their results.

    Commands are matched by substring so tests can key off the interesting part
    of a command line without restating it verbatim.
    """

    def __init__(self,
                 failures=None,
                 timeouts=None,
                 build='octopus/R156',
                 raises=None,
                 df_output=_ROOMY_DF,
                 du_output=_EXISTING_TREE_DU):
        self.hostname = 'fake-dut'
        self.commands = []
        self._failures = failures or {}
        self._timeouts = timeouts or ()
        self._raises = raises or ()
        self._df_output = df_output
        self._du_output = du_output
        self.host_info_store = mock.Mock()
        self.host_info_store.get.return_value = mock.Mock(build=build)

    def run(self,
            cmd,
            timeout=None,
            ignore_status=False,
            ignore_timeout=False):
        """Records the command and returns its scripted result."""
        self.commands.append(cmd)
        for needle in self._raises:
            if needle in cmd:
                raise Exception('ssh exploded')
        for needle in self._timeouts:
            if needle in cmd:
                return None
        for needle, failure in self._failures.items():
            if needle in cmd:
                if isinstance(failure, tuple):
                    status, stdout = failure
                else:
                    status, stdout = failure, ''
                return FakeCmdResult(exit_status=status,
                                     stdout=stdout,
                                     stderr='boom')
        if cmd.startswith('df '):
            return FakeCmdResult(stdout=self._df_output)
        if cmd.startswith('du '):
            return FakeCmdResult(stdout=self._du_output)
        return FakeCmdResult()

    def ran(self, needle):
        """Returns whether any recorded command contains the given substring."""
        return any(needle in cmd for cmd in self.commands)

    def matching(self, needle):
        """Returns every recorded command containing the given substring."""
        return [cmd for cmd in self.commands if needle in cmd]

    def index_of(self, needle):
        """Returns the position of the first command containing the needle.

        @raises AssertionError: If no command matches.
        """
        for i, cmd in enumerate(self.commands):
            if needle in cmd:
                return i
        raise AssertionError('No command matched %r in:\n%s' %
                             (needle, '\n'.join(self.commands)))


# Absolute path of a sentinel under the real install dir. Used to simulate "no
# Telemetry on the DUT". It must be absolute: the bare relative path would also
# match the identically-named sentinel under the staging directory, which would
# make the deploy look like it had failed verification.
_ABSENT = telemetry_deploy.TELEMETRY_DUT_SRC + '/tools/perf/run_benchmark'

# Exact commands, so ordering assertions cannot latch onto the wrong rm or .pth
# check.
_RM_LIVE_TREE = "rm -rf '%s'" % telemetry_deploy.TELEMETRY_DUT_DIR
_SWAP = "mv '%s' '%s'" % (telemetry_deploy._STAGING_DIR,
                          telemetry_deploy.TELEMETRY_DUT_DIR)
_WRITE_PTH = 'umask 022;'

# The two payload formats a build may publish. Looked up by suffix rather than
# by position, because the module expects the candidate order to be swapped
# once builds publish zstd, and that must not silently rebind these names to
# the wrong format. The order itself is pinned by PayloadFormatTest.
_BZ2, _ZST = [
        next(name for name in telemetry_deploy.TELEMETRY_DEP_TARBALLS
             if name.endswith(suffix)) for suffix in ('.bz2', '.zst')
]

# Scripted FakeHost failure for a curl --fail -w '%{http_code}' HTTP 404.
_HTTP_404 = (packages.CURL_HTTP_ERROR_EXIT, '404')


class IsTelemetryOnDutTest(unittest.TestCase):
    """Tests for is_telemetry_on_dut."""

    def test_present(self):
        host = FakeHost()
        self.assertTrue(telemetry_deploy.is_telemetry_on_dut(host))

    def test_absent(self):
        host = FakeHost(failures={_ABSENT: 1})
        self.assertFalse(telemetry_deploy.is_telemetry_on_dut(host))

    def test_timeout_counts_as_absent(self):
        host = FakeHost(timeouts=(_ABSENT, ))
        self.assertFalse(telemetry_deploy.is_telemetry_on_dut(host))

    def test_unreachable_dut_counts_as_absent(self):
        """The deploy that follows reports the connection problem properly."""
        host = FakeHost(raises=(_ABSENT, ))
        self.assertFalse(telemetry_deploy.is_telemetry_on_dut(host))

    def test_checks_every_sentinel(self):
        host = FakeHost()
        telemetry_deploy.is_telemetry_on_dut(host)
        for path in telemetry_deploy._SENTINEL_PATHS:
            self.assertTrue(host.ran(path))

    def test_checks_under_the_install_dir(self):
        """The probe must look under the live tree, not a scratch dir."""
        host = FakeHost()
        telemetry_deploy.is_telemetry_on_dut(host)
        self.assertTrue(host.ran(telemetry_deploy.TELEMETRY_DUT_SRC))
        self.assertFalse(host.ran(telemetry_deploy._STAGING_DIR))

    # pylint: disable=protected-access
    def test_shell_checks_both_sentinels_and_rootfs_or_stateful_pth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            rootfs_lib = os.path.join(usr, 'lib', 'python3.11',
                                      'site-packages')
            rootfs_lib64 = os.path.join(usr, 'lib64', 'python3.11',
                                        'site-packages')
            os.makedirs(rootfs_lib)
            # A stray /usr/lib64/python3.*/site-packages alongside /usr/lib on
            # a --with-platlibdir=lib build must not fail the presence check
            # when /usr/lib/python3.*/site-packages/telemetry.pth is present.
            os.makedirs(rootfs_lib64)
            src = os.path.join(usr, 'local', 'telemetry', 'src')
            for rel in telemetry_deploy._SENTINEL_PATHS:
                full = os.path.join(src, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w', encoding='utf-8') as f:
                    f.write('x\n')

            host = _ShellHost(tmpdir)
            with mock.patch.object(telemetry_deploy,
                                   'TELEMETRY_DUT_SRC',
                                   new=src):
                # Tree present, but neither rootfs nor stateful .pth exists.
                self.assertFalse(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))

                # Pre-installed image: rootfs .pth in lib/ satisfies the check
                # even when an unpopulated lib64/ site-packages dir also exists.
                rootfs_pth = os.path.join(rootfs_lib,
                                          telemetry_deploy._PTH_FILENAME)
                with open(rootfs_pth, 'w', encoding='utf-8') as f:
                    f.write(telemetry_deploy._TELEMETRY_PYTHON_PATH + '\n')
                self.assertTrue(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))

                # Option A image: rootfs .pth exists, but live tree is absent.
                sentinel = os.path.join(src,
                                        telemetry_deploy._SENTINEL_PATHS[0])
                os.unlink(sentinel)
                self.assertFalse(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))

                # On-demand deploy: no rootfs .pth, stateful .pth satisfies it.
                with open(sentinel, 'w', encoding='utf-8') as f:
                    f.write('x\n')
                os.unlink(rootfs_pth)
                telemetry_deploy._ensure_python_path(host, rootfs_prefix=usr)
                self.assertTrue(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))

    # pylint: enable=protected-access


class DecompressFlagTest(unittest.TestCase):
    """Tests for _decompress_flag."""

    def test_known_formats(self):
        for name, expected in (('x.tar.bz2', '-j'), ('x.tar.zst', '--zstd'),
                               ('x.tar.gz', '-z'), ('x.tar', '')):
            self.assertEqual(telemetry_deploy._decompress_flag(name), expected)

    def test_unknown_format(self):
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy._decompress_flag('x.tar.lz4')


class RunTest(unittest.TestCase):
    """Tests for _run, the module's only error-surfacing path."""

    def test_returns_result_on_success(self):
        host = FakeHost()
        result = telemetry_deploy._run(host, 'true', 10, 'Doing a thing')
        self.assertEqual(result.exit_status, 0)

    def test_failure_message_names_the_step_and_host(self):
        host = FakeHost(failures={'false': 3})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy._run(host, 'false', 10, 'Doing a thing')
        message = str(cm.exception)
        self.assertIn('Doing a thing', message)
        self.assertIn('fake-dut', message)
        self.assertIn('3', message)
        self.assertIn('boom', message)

    def test_timeout_message_names_the_timeout(self):
        host = FakeHost(timeouts=('sleep', ))
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy._run(host, 'sleep 99', 42, 'Waiting')
        message = str(cm.exception)
        self.assertIn('Waiting', message)
        self.assertIn('42', message)


class RunBestEffortTest(unittest.TestCase):
    """Cleanup must never raise, whatever the DUT does."""

    def test_swallows_failure(self):
        host = FakeHost(failures={'rm': 1})
        telemetry_deploy._run_best_effort(host, 'rm -rf /x', 10, 'Cleaning')

    def test_swallows_timeout(self):
        host = FakeHost(timeouts=('rm', ))
        telemetry_deploy._run_best_effort(host, 'rm -rf /x', 10, 'Cleaning')

    def test_swallows_host_exception(self):
        """host.run() itself raises if the DUT went away mid-test."""
        host = FakeHost(raises=('rm', ))
        telemetry_deploy._run_best_effort(host, 'rm -rf /x', 10, 'Cleaning')

    def test_passes_ignore_flags(self):
        host = mock.Mock()
        telemetry_deploy._run_best_effort(host, 'rm -rf /x', 10, 'Cleaning')
        _, kwargs = host.run.call_args
        self.assertTrue(kwargs['ignore_status'])
        self.assertTrue(kwargs['ignore_timeout'])


@mock.patch.object(telemetry_deploy, '_dep_url', return_value='http://cache/x')
@mock.patch.object(telemetry_deploy, '_deploy_enabled', return_value=True)
class EnsureTest(unittest.TestCase):
    """Tests for ensure_telemetry_on_dut."""

    def test_noop_when_present(self, _enabled, dep_url):
        host = FakeHost()
        self.assertFalse(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertFalse(host.ran('curl'))
        self.assertFalse(dep_url.called)

    def test_deploys_when_absent(self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        self.assertTrue(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertTrue(host.ran('curl'))
        self.assertTrue(host.ran('tar x'))

    def test_extraction_prunes_what_the_image_pruned(self, _enabled, _dep_url):
        """Unused upstream test fixtures and corpora must be excluded."""
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        tar = host.matching('tar x')[0]
        for path in telemetry_deploy._EXCLUDED_PATHS:
            self.assertIn("--exclude='%s'" % path, tar)

    # pylint: disable=protected-access
    def test_deploy_writes_pth_before_swap(self, _enabled, _dep_url):
        """telemetry.pth must be written before _swap_into_place commits."""
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        pth_idx = host.index_of(_WRITE_PTH)
        self.assertGreater(pth_idx, host.index_of('tar x'))
        self.assertLess(pth_idx, host.index_of(_SWAP))
        cmd = host.commands[pth_idx]
        self.assertIn(telemetry_deploy._PTH_FILENAME, cmd)
        self.assertIn(telemetry_deploy._SITE_PACKAGES_REL_GLOB, cmd)
        self.assertIn('$prefix/local/${d#"$prefix"/}', cmd)
        self.assertIn(telemetry_deploy._TELEMETRY_PYTHON_PATH, cmd)

    # pylint: enable=protected-access

    def test_force_deploys_over_existing_tree(self, _enabled, _dep_url):
        host = FakeHost()
        self.assertTrue(
                telemetry_deploy.ensure_telemetry_on_dut(host, force=True))
        self.assertTrue(host.ran('curl'))

    def test_existing_tree_removed_between_download_and_extract(
            self, _enabled, _dep_url):
        """Nothing is staged alongside the old tree: no room for both.

        The removal is deliberately as late as possible, so a failure to obtain
        the payload leaves the DUT exactly as it was.
        """
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertLess(host.index_of('curl'), host.index_of(_RM_LIVE_TREE))
        self.assertLess(host.index_of(_RM_LIVE_TREE), host.index_of('tar x'))

    def test_uses_explicit_build(self, _enabled, dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host, build='eve/R100')
        self.assertEqual(dep_url.call_args[0][1], 'eve/R100')

    def test_falls_back_to_host_info_build(self, _enabled, dep_url):
        host = FakeHost(failures={_ABSENT: 1}, build='eve/R100')
        telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertEqual(dep_url.call_args[0][1], 'eve/R100')

    def test_bucket_defaults_to_telemetry_setup(self, _enabled, dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertEqual(dep_url.call_args[0][3],
                         telemetry_deploy.telemetry_setup.DEFAULT_DEPS_BUCKET)

    def test_bucket_override_is_honoured(self, _enabled, dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host, bucket='other-bucket')
        self.assertEqual(dep_url.call_args[0][3], 'other-bucket')

    def test_missing_build_raises(self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1}, build=None)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)

    def test_download_failure_raises(self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1, 'curl': 22})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)

    def test_missing_sentinel_raises(self, _enabled, _dep_url):
        staged = telemetry_deploy._STAGING_DIR + '/src/tools/perf'
        host = FakeHost(failures={_ABSENT: 1, staged: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)

    def test_unpruned_tree_raises(self, _enabled, _dep_url):
        """An --exclude that stopped matching costs ~420 MiB, silently.

        Nothing else in the system would ever notice, which is the whole
        reason the staged tree is checked for absence as well as presence.
        """
        leftover = "! -e '%s/src/%s'" % (telemetry_deploy._STAGING_DIR,
                                         telemetry_deploy._PRUNED_PATHS[0])
        host = FakeHost(failures={_ABSENT: 1, leftover: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertIn('excluded', str(cm.exception))
        self.assertFalse(host.ran(_SWAP))


@mock.patch.object(telemetry_deploy, '_dep_url', return_value='http://cache/x')
class KillSwitchTest(unittest.TestCase):
    """The kill switch must not turn into an undiagnosable test failure.

    Turning it off is how an operator says "go back to relying on the image".
    On an image that ships Telemetry that is free; on one that does not, the
    test cannot work, and saying so beats letting run_benchmark fail with
    'No such file or directory' several minutes later.
    """

    def setUp(self):
        patcher = mock.patch.object(telemetry_deploy,
                                    '_deploy_enabled',
                                    return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_disabled_and_present_is_a_quiet_noop(self, dep_url):
        host = FakeHost()
        self.assertFalse(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertFalse(host.ran('curl'))
        self.assertFalse(dep_url.called)

    def test_disabled_and_absent_raises_naming_the_config_key(self, _dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertIn(telemetry_deploy._ENABLE_CONFIG_KEY, str(cm.exception))
        self.assertFalse(host.ran('curl'))

    def test_disabled_under_force_raises(self, _dep_url):
        """force means "I need a deploy", which the switch has forbidden."""
        host = FakeHost()
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host, force=True)

    def test_disabled_never_modifies_the_dut(self, _dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertFalse(any('rm -rf' in cmd for cmd in host.commands))


@mock.patch.object(telemetry_deploy, '_dep_url', return_value='http://cache/x')
@mock.patch.object(telemetry_deploy, '_deploy_enabled', return_value=True)
class DutStateOnFailureTest(unittest.TestCase):
    """Pins what a failed deploy leaves behind.

    Two separate promises: no partial tree is stranded on the partition this
    change exists to free up, and the DUT's existing Telemetry is not destroyed
    for a deploy that was never going to succeed.
    """

    def _scratch_removals(self, host):
        """Returns removals issued after extraction started."""
        started = host.index_of('tar x')
        return [
                cmd for cmd in host.commands[started:]
                if telemetry_deploy._STAGING_DIR in cmd and 'rm -rf' in cmd
        ]

    def test_staging_removed_when_verification_fails(self, _enabled, _dep_url):
        staged = telemetry_deploy._STAGING_DIR + '/src/tools/perf'
        host = FakeHost(failures={_ABSENT: 1, staged: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertTrue(self._scratch_removals(host))

    # pylint: disable=protected-access
    def test_staging_removed_when_swap_fails(self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1, _SWAP: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertTrue(self._scratch_removals(host))

    def test_pth_failure_does_not_strand_the_live_tree(self, _enabled,
                                                       _dep_url):
        host = FakeHost(failures={
                _ABSENT: 1,
                _WRITE_PTH: 1,
        })
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertFalse(host.ran(_SWAP))
        self.assertTrue(self._scratch_removals(host))

    # pylint: enable=protected-access

    def test_tarball_removed_on_success(self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertTrue(
                any(telemetry_deploy._SCRATCH_DIR in cmd and 'rm -rf' in cmd
                    for cmd in host.commands))

    def test_existing_tree_survives_a_download_failure(self, _enabled,
                                                       _dep_url):
        """A 404 or a dead network must not cost the DUT its Telemetry."""
        host = FakeHost(failures={_ABSENT: 1, 'curl': 22})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertFalse(host.ran(_RM_LIVE_TREE))

    def test_existing_tree_survives_an_unresolvable_build(
            self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1}, build=None)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertFalse(host.ran(_RM_LIVE_TREE))


@mock.patch.object(telemetry_deploy, '_dep_url', return_value='http://cache/x')
@mock.patch.object(telemetry_deploy, '_deploy_enabled', return_value=True)
class FreeSpaceTest(unittest.TestCase):
    """Tests for the pre-flight free space check."""

    # 0.5 GiB free: not even the compressed payload fits.
    _CRAMPED = ('Filesystem  1024-blocks     Used Available Capacity Mounted\n'
                '/dev/sda1      20000000 19500000    500000      98% /mnt\n')

    # Exactly 1 GiB free: room for the payload, but not for the payload and
    # the extracted tree together, even after the old tree is reclaimed.
    _TIGHT = ('Filesystem  1024-blocks     Used Available Capacity Mounted\n'
              '/dev/sda1      20000000 18951424   1048576      95% /mnt\n')

    def test_payload_alone_must_fit_before_the_dut_is_touched(
            self, _enabled, _dep_url):
        host = FakeHost(failures={_ABSENT: 1},
                        df_output=self._CRAMPED,
                        du_output=_NO_TREE_DU)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertIn('free', str(cm.exception))
        self.assertFalse(host.ran('curl'))
        self.assertFalse(host.ran(_RM_LIVE_TREE))

    def test_reclaimable_tree_does_not_pay_for_the_download(
            self, _enabled, _dep_url):
        """The download runs first, so the old tree's space is not its to use.

        Counting it would let a DUT start a 658 MiB download it has nowhere to
        put, and only find out part way through.
        """
        host = FakeHost(failures={_ABSENT: 1}, df_output=self._CRAMPED)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertIn('Downloading', str(cm.exception))
        self.assertFalse(host.ran('curl'))

    def test_existing_tree_counts_as_available_for_the_extraction(
            self, _enabled, _dep_url):
        """It is deleted before extraction, so its space is about to be free."""
        host = FakeHost(failures={_ABSENT: 1}, df_output=self._TIGHT)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        # 1.0 GiB free plus 1.2 GiB reclaimable is still short of the 2.5 GiB
        # floor, but the message must report the larger, honest number.
        self.assertIn('2.2 GiB', str(cm.exception))

    def test_failed_df_does_not_block_the_deploy(self, _enabled, _dep_url):
        """df exits non-zero for a path it cannot stat; that is not fatal."""
        host = FakeHost(failures={_ABSENT: 1, 'df -Pk': 1})
        self.assertTrue(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertTrue(host.ran('curl'))

    def test_unparseable_df_does_not_block_the_deploy(self, _enabled,
                                                      _dep_url):
        """A surprising df is not a reason to fail; let tar speak instead."""
        host = FakeHost(failures={_ABSENT: 1}, df_output='what is a disk\n')
        self.assertTrue(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertTrue(host.ran('curl'))

    def test_failed_du_counts_as_nothing_reclaimable(self, _enabled, _dep_url):
        """du exits non-zero when there is no tree, which means no headroom."""
        host = FakeHost(failures={
                _ABSENT: 1,
                'du -sk': 1
        },
                        df_output=self._TIGHT)
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertIn('1.0 GiB', str(cm.exception))

    def test_unmeasurable_du_does_not_block_the_deploy(self, _enabled,
                                                       _dep_url):
        """A timeout on a cold multi-gigabyte tree is not the same as absent.

        Treating it as zero reclaimable would refuse deploys on DUTs that have
        ample room once the old tree goes.
        """
        host = FakeHost(failures={_ABSENT: 1},
                        timeouts=('du -sk', ),
                        df_output=self._TIGHT)
        self.assertTrue(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertTrue(host.ran('curl'))


class SwapIntoPlaceTest(unittest.TestCase):
    """Tests for _swap_into_place, the riskiest command in the module."""

    def test_staged_tree_is_renamed_onto_the_live_path(self):
        host = FakeHost()
        telemetry_deploy._swap_into_place(host)
        cmd = host.commands[0]
        self.assertIn(_SWAP, cmd)
        # The rm guards against mv nesting the staged tree inside a directory
        # that reappeared after _remove_existing_tree ran.
        self.assertTrue(cmd.startswith(_RM_LIVE_TREE))

    def test_swap_happens_after_verification(self):
        """Nothing may be published before the staged tree is verified."""
        host = FakeHost(failures={_ABSENT: 1})
        with mock.patch.object(telemetry_deploy,
                               '_deploy_enabled',
                               return_value=True):
            with mock.patch.object(telemetry_deploy,
                                   '_dep_url',
                                   return_value='http://cache/x'):
                telemetry_deploy.ensure_telemetry_on_dut(host)
        staged_sentinel = telemetry_deploy._STAGING_DIR + '/src'
        verification = max(
                i for i, cmd in enumerate(host.commands)
                if cmd.startswith('test ') and staged_sentinel in cmd)
        self.assertLess(verification, host.index_of(_SWAP))

    def test_failure_is_reported(self):
        host = FakeHost(failures={_SWAP: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy._swap_into_place(host)


# pylint: disable=protected-access
class _ShellHost(FakeHost):
    """FakeHost that executes commands against the local /bin/sh."""

    def __init__(self, allowed_prefix):
        super().__init__()
        self._allowed_prefix = allowed_prefix

    def run(self,
            cmd,
            timeout=None,
            ignore_status=False,
            ignore_timeout=False):
        """Executes cmd under /bin/sh after guarding against host paths."""
        if (self._allowed_prefix not in cmd
                    or telemetry_deploy._DUT_FILESYSTEM in cmd):
            raise AssertionError(
                    'Refusing to run command without patched temp prefix %r: '
                    '%s' % (self._allowed_prefix, cmd))
        self.commands.append(cmd)
        proc = subprocess.run(['/bin/sh', '-c', cmd],
                              check=False,
                              timeout=timeout or 10,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True)
        return FakeCmdResult(exit_status=proc.returncode,
                             stdout=proc.stdout,
                             stderr=proc.stderr)


class PythonPathTest(unittest.TestCase):
    """Tests for _ensure_python_path."""

    def test_mirrors_lib_and_lib64_site_packages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            for libdir in ('lib', 'lib64'):
                os.makedirs(
                        os.path.join(usr, libdir, 'python3.11',
                                     'site-packages'))
            # Non-directory matches must be skipped rather than breaking mkdir.
            os.makedirs(os.path.join(usr, 'libexec', 'python3.11'))
            with open(os.path.join(usr, 'libexec', 'python3.11',
                                   'site-packages'),
                      'w',
                      encoding='utf-8') as f:
                f.write('not a dir\n')

            prev_umask = os.umask(0o077)
            self.addCleanup(os.umask, prev_umask)

            host = _ShellHost(tmpdir)
            telemetry_deploy._ensure_python_path(host, rootfs_prefix=usr + '/')

            expected = telemetry_deploy._TELEMETRY_PYTHON_PATH + '\n'
            for libdir in ('lib', 'lib64'):
                local_site = os.path.join(usr, 'local', libdir, 'python3.11',
                                          'site-packages')
                pth = os.path.join(local_site, telemetry_deploy._PTH_FILENAME)
                self.assertEqual(os.stat(local_site).st_mode & 0o777, 0o755)
                self.assertEqual(os.stat(pth).st_mode & 0o777, 0o644)
                with open(pth, encoding='utf-8') as f:
                    self.assertEqual(f.read(), expected)
            self.assertFalse(
                    os.path.exists(
                            os.path.join(usr, 'local', 'libexec', 'python3.11',
                                         'site-packages')))

    def test_replaces_symlink_without_clobbering_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            os.makedirs(
                    os.path.join(usr, 'lib64', 'python3.11', 'site-packages'))
            local_site = os.path.join(usr, 'local', 'lib64', 'python3.11',
                                      'site-packages')
            os.makedirs(local_site)
            victim = os.path.join(tmpdir, 'victim.txt')
            with open(victim, 'w', encoding='utf-8') as f:
                f.write('keep-me\n')
            pth = os.path.join(local_site, telemetry_deploy._PTH_FILENAME)
            os.symlink(victim, pth)

            telemetry_deploy._ensure_python_path(_ShellHost(tmpdir),
                                                 rootfs_prefix=usr)

            with open(victim, encoding='utf-8') as f:
                self.assertEqual(f.read(), 'keep-me\n')
            self.assertFalse(os.path.islink(pth))
            with open(pth, encoding='utf-8') as f:
                self.assertEqual(
                        f.read(),
                        telemetry_deploy._TELEMETRY_PYTHON_PATH + '\n')

    def test_replaces_directory_at_pth_destination(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            os.makedirs(
                    os.path.join(usr, 'lib64', 'python3.11', 'site-packages'))
            pth_dir = os.path.join(usr, 'local', 'lib64', 'python3.11',
                                   'site-packages',
                                   telemetry_deploy._PTH_FILENAME)
            os.makedirs(pth_dir)
            with open(os.path.join(pth_dir, 'stale'), 'w',
                      encoding='utf-8') as f:
                f.write('stale\n')

            telemetry_deploy._ensure_python_path(_ShellHost(tmpdir),
                                                 rootfs_prefix=usr)

            self.assertTrue(os.path.isfile(pth_dir))
            with open(pth_dir, encoding='utf-8') as f:
                self.assertEqual(
                        f.read(),
                        telemetry_deploy._TELEMETRY_PYTHON_PATH + '\n')

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses 0500 directory mode')
    def test_cleans_up_temp_file_when_write_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            os.makedirs(
                    os.path.join(usr, 'lib64', 'python3.11', 'site-packages'))
            local_site = os.path.join(usr, 'local', 'lib64', 'python3.11',
                                      'site-packages')
            os.makedirs(local_site)
            os.chmod(local_site, 0o500)
            try:
                with self.assertRaises(telemetry_deploy.TelemetryDeployError):
                    telemetry_deploy._ensure_python_path(_ShellHost(tmpdir),
                                                         rootfs_prefix=usr)
                self.assertEqual(os.listdir(local_site), [])
            finally:
                os.chmod(local_site, 0o700)

    def test_fails_when_no_site_packages_dir_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            os.makedirs(usr)
            with self.assertRaises(
                    telemetry_deploy.TelemetryDeployError) as cm:
                telemetry_deploy._ensure_python_path(_ShellHost(tmpdir),
                                                     rootfs_prefix=usr)
            self.assertIn('No directory matched', str(cm.exception))

    def test_failure_raises_deploy_error(self):
        host = FakeHost(failures={_WRITE_PTH: 1})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError):
            telemetry_deploy._ensure_python_path(host)


class CleanupTest(unittest.TestCase):
    """Tests for cleanup_telemetry_on_dut."""

    def test_removes_tree_and_scratch_dirs(self):
        host = FakeHost()
        telemetry_deploy.cleanup_telemetry_on_dut(host)
        self.assertEqual(len(host.commands), 1)
        cmd = host.commands[0]
        self.assertTrue(cmd.startswith('rm -rf '))
        for path in (telemetry_deploy.TELEMETRY_DUT_DIR,
                     telemetry_deploy._STAGING_DIR,
                     telemetry_deploy._SCRATCH_DIR):
            self.assertIn(path, cmd)
        self.assertNotIn(telemetry_deploy._PTH_FILENAME, cmd)

    def test_preserves_symlinked_test_image_pth_on_cleanup(self):
        """On ChromeOS test images, /usr/lib/python3.* symlinks into stateful.

        Specifically, /usr/lib/python3.11 -> /usr/local/usr/lib/python3.11 and
        /usr/local/usr -> ., so /usr/lib/python3.11/site-packages/telemetry.pth
        and /usr/local/lib/python3.11/site-packages/telemetry.pth are the same
        inode. Cleanup must remove the deployed tree without deleting the
        shared telemetry.pth file, while is_telemetry_on_dut() must return
        False once the tree is gone.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            usr = os.path.join(tmpdir, 'usr')
            local_site = os.path.join(usr, 'local', 'lib', 'python3.11',
                                      'site-packages')
            os.makedirs(local_site)
            os.symlink('.', os.path.join(usr, 'local', 'usr'))
            os.makedirs(os.path.join(usr, 'lib'))
            os.symlink(os.path.join(usr, 'local', 'usr', 'lib', 'python3.11'),
                       os.path.join(usr, 'lib', 'python3.11'))

            host = _ShellHost(tmpdir)
            telemetry_deploy._ensure_python_path(host, rootfs_prefix=usr)
            rootfs_pth = os.path.join(usr, 'lib', 'python3.11',
                                      'site-packages',
                                      telemetry_deploy._PTH_FILENAME)
            local_pth = os.path.join(local_site,
                                     telemetry_deploy._PTH_FILENAME)
            self.assertTrue(os.path.samefile(rootfs_pth, local_pth))

            tree = os.path.join(usr, 'local', 'telemetry')
            src = os.path.join(tree, 'src')
            for rel in telemetry_deploy._SENTINEL_PATHS:
                full = os.path.join(src, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w', encoding='utf-8') as f:
                    f.write('x\n')
            staging = tree + '.staging'
            scratch = os.path.join(tmpdir, 'scratch')
            for path in (staging, scratch):
                os.makedirs(path)

            with mock.patch.multiple(telemetry_deploy,
                                     TELEMETRY_DUT_DIR=tree,
                                     TELEMETRY_DUT_SRC=src,
                                     _SCRATCH_PATHS=(scratch, staging)):
                self.assertTrue(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))
                telemetry_deploy.cleanup_telemetry_on_dut(host)
                self.assertFalse(os.path.exists(tree))
                self.assertTrue(os.path.exists(rootfs_pth))
                self.assertFalse(
                        telemetry_deploy.is_telemetry_on_dut(
                                host, rootfs_prefix=usr))

    def test_never_raises(self):
        """Cleanup runs while an exception may already be propagating."""
        host = FakeHost(raises=('rm -rf', ))
        telemetry_deploy.cleanup_telemetry_on_dut(host)
# pylint: enable=protected-access


class DepUrlTest(unittest.TestCase):
    """Tests for _dep_url."""

    # Reproduces the layout verified in GCS, where the dep only exists inside
    # gs://chromeos-image-archive/octopus-release/R156-16825.0.0/.
    _EXPECTED = ('http://cache:8082/extract/chromeos-image-archive/'
                 'octopus-release/R156-16825.0.0/autotest_packages.tar'
                 '?file=autotest/packages/dep-telemetry_dep.tar.bz2')

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_uses_explicit_cache_endpoint(self, image_server):
        image_server.return_value.url.return_value = 'http://cache:8082'
        url = telemetry_deploy._dep_url(FakeHost(),
                                        'octopus-release/R156-16825.0.0',
                                        'cache:8082', 'chromeos-image-archive',
                                        _BZ2)
        image_server.assert_called_once_with('http://cache:8082')
        self.assertEqual(url, self._EXPECTED)

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_resolves_devserver_when_no_endpoint(self, image_server):
        image_server.resolve.return_value.url.return_value = (
                'http://cache:8082')
        host = FakeHost()
        url = telemetry_deploy._dep_url(host, 'octopus-release/R156-16825.0.0',
                                        None, 'chromeos-image-archive', _BZ2)
        image_server.resolve.assert_called_once_with(
                'octopus-release/R156-16825.0.0', hostname=host.hostname)
        self.assertEqual(url, self._EXPECTED)

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_tarball_name_appears_in_the_url(self, image_server):
        """Each candidate format has to ask for its own file name."""
        image_server.return_value.url.return_value = 'http://cache:8082'
        url = telemetry_deploy._dep_url(FakeHost(), 'octopus/R1', 'cache:8082',
                                        'chromeos-image-archive', _ZST)
        self.assertTrue(url.endswith('?file=autotest/packages/%s' % _ZST), url)

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_bucket_appears_in_the_url(self, image_server):
        image_server.return_value.url.return_value = 'http://cache:8082'
        url = telemetry_deploy._dep_url(FakeHost(), 'octopus/R1', 'cache:8082',
                                        'staging-chromeos-image-archive', _BZ2)
        self.assertIn('staging-chromeos-image-archive', url)

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_endpoint_scheme_is_not_doubled(self, image_server):
        """CFT may hand down an endpoint that already carries a scheme."""
        image_server.return_value.url.return_value = 'http://cache:8082'
        telemetry_deploy._dep_url(FakeHost(), 'octopus/R1',
                                  'http://cache:8082',
                                  'chromeos-image-archive', _BZ2)
        image_server.assert_called_once_with('http://cache:8082')

    @mock.patch.object(telemetry_deploy.dev_server, 'ImageServer')
    def test_unresolvable_devserver_raises_deploy_error(self, image_server):
        """DevServerException is a bare Exception, so it must be converted."""
        for exc in (
                telemetry_deploy.dev_server.DevServerException,
                telemetry_deploy.dev_server.DevServerFailToLocateException):
            image_server.resolve.side_effect = exc('everything is down')
            with self.assertRaises(telemetry_deploy.TelemetryDeployError) as e:
                telemetry_deploy._dep_url(FakeHost(), 'octopus/R1', None,
                                          'chromeos-image-archive', _BZ2)
            self.assertIn('everything is down', str(e.exception))


class DownloadTest(unittest.TestCase):
    """The DUT unpacks this payload as root, so the fetch is constrained."""

    def _curl(self):
        host = FakeHost()
        telemetry_deploy._download_one(host, 'http://cache/x', '/tmp/x', _BZ2)
        return host.matching('curl')[0]

    def test_redirects_are_bounded(self):
        curl = self._curl()
        self.assertIn("--proto '=http,https'", curl)
        self.assertIn('--max-redirs 3', curl)

    def test_curl_owns_the_deadline(self):
        """Otherwise retried stalls run out the ssh budget instead."""
        curl = self._curl()
        self.assertIn(
                '--max-time %d' % telemetry_deploy._CURL_MAX_TIME_SECONDS,
                curl)
        self.assertLess(telemetry_deploy._CURL_MAX_TIME_SECONDS,
                        telemetry_deploy._DOWNLOAD_TIMEOUT_SECONDS)

    def test_destination_and_url_are_separate_operands(self):
        curl = self._curl()
        self.assertIn("--output '/tmp/x' 'http://cache/x'", curl)

    def test_http_error_is_reported_as_a_missing_payload(self):
        """Distinguishing a 404/410 from a 5xx/network error enables fallback."""
        for code in ('404', '410'):
            host = FakeHost(
                    failures={'curl': (packages.CURL_HTTP_ERROR_EXIT, code)})
            with self.assertRaises(telemetry_deploy._PayloadNotFound):
                telemetry_deploy._download_one(host, 'http://cache/x',
                                               '/tmp/x', _BZ2)

    def test_http_5xx_is_not_reported_as_missing(self):
        """A 503 after retries is a server failure, not an unpublished format."""
        host = FakeHost(
                failures={'curl': (packages.CURL_HTTP_ERROR_EXIT, '503')})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy._download_one(host, 'http://cache/x', '/tmp/x',
                                           _BZ2)
        self.assertNotIsInstance(cm.exception,
                                 telemetry_deploy._PayloadNotFound)

    def test_other_failures_are_not_reported_as_missing(self):
        host = FakeHost(failures={'curl': 7})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy._download_one(host, 'http://cache/x', '/tmp/x',
                                           _BZ2)
        self.assertNotIsInstance(cm.exception,
                                 telemetry_deploy._PayloadNotFound)


def _fake_dep_url(_host, _build, _endpoint, _bucket, tarball):
    """Stands in for the cache server, with a URL per candidate format."""
    return 'http://cache/%s' % tarball


@mock.patch.object(telemetry_deploy, '_dep_url', side_effect=_fake_dep_url)
@mock.patch.object(telemetry_deploy, '_deploy_enabled', return_value=True)
class PayloadFormatTest(unittest.TestCase):
    """A build publishes exactly one compression format, so both are tried.

    Builds are moving from bzip2 to zstd, and the two coexist for as long as
    the oldest build a drone at HEAD still has to test. Asking for only one
    name would fail every deploy on the other half of that window.
    """

    def _requested(self, dep_url):
        """Returns the candidate names asked for, in order."""
        return [call[0][4] for call in dep_url.call_args_list]

    def test_zstd_is_tried_first_today(self, _enabled, _dep_url):
        """New builds publish zstd; bzip2 is a fallback for older ones."""
        self.assertEqual(telemetry_deploy.TELEMETRY_DEP_TARBALLS, (_ZST, _BZ2))

    def test_second_candidate_is_not_fetched_when_the_first_works(
            self, _enabled, dep_url):
        host = FakeHost(failures={_ABSENT: 1})
        telemetry_deploy.ensure_telemetry_on_dut(host)
        self.assertEqual(self._requested(dep_url), [_ZST])
        self.assertIn(' --zstd ', host.matching('tar x')[0])

    def test_falls_back_to_bzip2_when_zstd_is_not_published(
            self, _enabled, dep_url):
        """The case this fallback exists for: a build from before the switch."""
        host = FakeHost(failures={_ABSENT: 1, _ZST: _HTTP_404})
        self.assertTrue(telemetry_deploy.ensure_telemetry_on_dut(host))
        self.assertEqual(self._requested(dep_url), [_ZST, _BZ2])
        tar = host.matching('tar x')[0]
        self.assertIn(' -j ', tar)
        self.assertIn(_BZ2, tar)

    def test_network_failure_does_not_try_the_next_format(
            self, _enabled, dep_url):
        """Only HTTP 404/410 means "this build does not publish that".

        Asking for a different file because the network or server is down
        would turn one clear failure into two confusing ones.
        """
        for failure in (7, (packages.CURL_HTTP_ERROR_EXIT, '503')):
            dep_url.reset_mock()
            host = FakeHost(failures={_ABSENT: 1, 'curl': failure})
            with self.assertRaises(telemetry_deploy.TelemetryDeployError):
                telemetry_deploy.ensure_telemetry_on_dut(host)
            self.assertEqual(self._requested(dep_url), [_ZST])

    def test_no_published_format_names_every_candidate(self, _enabled,
                                                       _dep_url):
        host = FakeHost(failures={_ABSENT: 1, 'curl': _HTTP_404})
        with self.assertRaises(telemetry_deploy.TelemetryDeployError) as cm:
            telemetry_deploy.ensure_telemetry_on_dut(host)
        for tarball in telemetry_deploy.TELEMETRY_DEP_TARBALLS:
            self.assertIn(tarball, str(cm.exception))

    def test_every_candidate_has_a_decompress_flag(self, _enabled, _dep_url):
        """A candidate the extractor cannot open would 404-loop to nowhere."""
        for tarball in telemetry_deploy.TELEMETRY_DEP_TARBALLS:
            telemetry_deploy._decompress_flag(tarball)


class QuotingTest(unittest.TestCase):
    """Commands run as root on the DUT and embed caller-supplied data."""

    def test_hostile_url_is_quoted(self):
        host = FakeHost(failures={_ABSENT: 1})
        hostile = 'http://cache/x;reboot'
        with mock.patch.object(telemetry_deploy,
                               '_deploy_enabled',
                               return_value=True):
            with mock.patch.object(telemetry_deploy,
                                   '_dep_url',
                                   return_value=hostile):
                telemetry_deploy.ensure_telemetry_on_dut(host)
        curl = host.matching('curl')[0]
        self.assertIn("'%s'" % hostile, curl)
        self.assertNotIn('x;reboot ', curl)


class ConfigKeyTest(unittest.TestCase):
    """Pins the kill switch to the key that actually ships in the ini.

    A typo in either place would leave the switch permanently at its default
    with nothing to show for it.
    """

    def test_key_is_present_in_global_config(self):
        value = global_config.global_config.get_config_value(
                'CROS',
                telemetry_deploy._ENABLE_CONFIG_KEY,
                type=bool,
                default=None)
        self.assertIsNotNone(
                value, '[CROS] %s is missing from global_config.ini' %
                telemetry_deploy._ENABLE_CONFIG_KEY)

    def test_enabled_by_default(self):
        self.assertTrue(telemetry_deploy._deploy_enabled())


if __name__ == '__main__':
    unittest.main()

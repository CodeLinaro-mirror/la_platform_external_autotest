#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for server/site_tests/telemetry_AFDOGenerate."""

# pylint: disable=protected-access

import os
import shlex
import subprocess
import sys
import types
import unittest
from unittest import mock

# Support running directly on Python 3.13+ hosts where PEP 594 removed stdlib
# `pipes` (`server/crashcollect.py` only calls `pipes.quote`, an alias for
# `shlex.quote`) without shadowing the real `pipes` module on older Python, and
# enable the pure-Python protobuf backend required by `frozen_chromite`.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
if sys.version_info >= (3, 13):
    sys.modules.setdefault("pipes", types.SimpleNamespace(quote=shlex.quote))

sys.path.insert(
        0,
        os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)
# pylint: disable=import-error,wrong-import-position
import common  # pylint: disable=unused-import
from autotest_lib.client.common_lib import error
from autotest_lib.server.site_tests.telemetry_AFDOGenerate import (
        telemetry_AFDOGenerate, )

# pylint: enable=import-error,wrong-import-position

# Keep direct `python3 -m unittest <path>` working alongside CQ's
# `./utils/unittest_suite.py`: `setup_modules` swaps parent packages in
# `sys.modules` during `import common`, hiding this module attribute otherwise.
setattr(
        sys.modules["autotest_lib.server.site_tests.telemetry_AFDOGenerate"],
        "telemetry_AFDOGenerate_unittest",
        sys.modules[__name__],
)


class _CmdResult:
    """Minimal stand-in for autotest CmdResult."""

    def __init__(self, exit_status=0, stdout="", stderr=""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


def _assert_valid_bash_syntax(cmd):
    """Validates that a generated command string parses cleanly in bash."""
    proc = subprocess.run(
            ["/bin/bash", "-n", "-c", cmd],
            capture_output=True,
            text=True,
            check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
                f"Invalid bash syntax in command:\n{cmd}\n{proc.stderr}")


class _FakeHost:
    """Scriptable host double recording full call metadata and bash validity."""

    def __init__(
            self,
            mktemp_stdout="/usr/local/tmp/telemetry_afdo.Ab12Cd\n",
            bg_pid="4321\n",
            started_after_polls=1,
            perf_out_stdout="[ perf record: Captured and wrote 12.3 MB ]\n",
            raises_on_substr=None,
            exit_status_on_substr=None,
    ):
        self.hostname = "fake-dut"
        self.port = 2222
        self.calls = []
        self.bg_commands = []
        self._mktemp_stdout = mktemp_stdout
        self._bg_pid = bg_pid
        self._started_after_polls = started_after_polls
        self._poll_count = 0
        self._perf_out_stdout = perf_out_stdout
        self._raises_on_substr = raises_on_substr or {}
        self._exit_status_on_substr = exit_status_on_substr or {}

    def run_background(self, cmd, verbose=False):
        """Records background command and returns fake PID."""
        del verbose
        _assert_valid_bash_syntax(cmd)
        self.bg_commands.append(cmd)
        self.calls.append(("run_background", cmd, {}))
        return self._bg_pid

    def run(
            self,
            cmd,
            timeout=None,
            ignore_status=False,
            ignore_timeout=False,
            verbose=False,
    ):
        """Records foreground command and returns scripted result."""
        _assert_valid_bash_syntax(cmd)
        kwargs = {
                "timeout": timeout,
                "ignore_status": ignore_status,
                "ignore_timeout": ignore_timeout,
                "verbose": verbose,
        }
        self.calls.append(("run", cmd, kwargs))

        for substr, exc in self._raises_on_substr.items():
            if substr in cmd:
                raise exc

        for substr, status in self._exit_status_on_substr.items():
            if substr in cmd:
                if status != 0 and not ignore_status:
                    raise error.AutoservRunError(cmd, _CmdResult(status))
                return _CmdResult(exit_status=status)

        if "mktemp -d" in cmd:
            return _CmdResult(exit_status=0, stdout=self._mktemp_stdout)
        if cmd.startswith("test -e "):
            self._poll_count += 1
            if (self._started_after_polls is not None
                        and self._poll_count >= self._started_after_polls):
                return _CmdResult(exit_status=0)
            return _CmdResult(exit_status=1)
        if cmd.startswith("cat "):
            return _CmdResult(exit_status=0, stdout=self._perf_out_stdout)
        return _CmdResult(exit_status=0)


def _make_test_instance(host, arch="amd64", profdir="/tmp/host_profdir"):
    """Builds telemetry_AFDOGenerate without calling base test.__init__."""
    afdo = telemetry_AFDOGenerate.telemetry_AFDOGenerate.__new__(
            telemetry_AFDOGenerate.telemetry_AFDOGenerate)
    afdo._host = host
    afdo._arch = arch
    afdo.profdir = profdir
    return afdo


class TelemetryAFDOGenerateUnittest(unittest.TestCase):
    """Tests _perf_on_dut directory lifecycle, arch flows, and error safety."""

    @mock.patch.object(telemetry_AFDOGenerate.utils, "run")
    def test_amd64_happy_path_sweeps_logs_output_and_cleans_up(
            self, mock_utils_run):
        mock_utils_run.return_value = _CmdResult(exit_status=0)
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.X86Run\n")
        afdo = _make_test_instance(host, arch="amd64")

        body_ran = False
        with afdo._perf_on_dut():
            body_ran = True
            self.assertEqual(len(host.bg_commands), 1)
            self.assertIn(
                    "-o /usr/local/tmp/telemetry_afdo.X86Run/perf.data",
                    host.bg_commands[0],
            )
            # Only the pre-run stale sweep should have run before exit, not
            # cleanup of the active run's directory.
            self.assertFalse(
                    any("telemetry_afdo.X86Run" in cmd and "rm -rf" in cmd
                        for _, cmd, _ in host.calls))

        self.assertTrue(body_ran)
        scp_cmd = mock_utils_run.call_args[0][0]
        _assert_valid_bash_syntax(scp_cmd)
        expected_src = (
                "'root@fake-dut:/usr/local/tmp/telemetry_afdo.X86Run/perf.data'"
        )
        self.assertIn(expected_src, scp_cmd)

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertIn("mkdir -p -m 1777 '/usr/local/tmp'", fg_cmds[0])
        self.assertIn("(killall -2 perf || true)", fg_cmds[0])
        self.assertIn("rm -rf '/usr/local/tmp/telemetry_afdo.'*", fg_cmds[0])
        self.assertIn("mktemp -d '/usr/local/tmp/telemetry_afdo.XXXXXX'",
                      fg_cmds[0])
        self.assertIn("cat /usr/local/tmp/telemetry_afdo.X86Run/perf_output",
                      fg_cmds)
        self.assertFalse(any("perf inject" in cmd for cmd in fg_cmds))

        cleanup_kind, cleanup_cmd, cleanup_kwargs = host.calls[-1]
        self.assertEqual(cleanup_kind, "run")
        self.assertEqual(cleanup_cmd,
                         "rm -rf '/usr/local/tmp/telemetry_afdo.X86Run'")
        self.assertEqual(cleanup_kwargs["timeout"], 60)
        self.assertTrue(cleanup_kwargs["ignore_status"])
        self.assertTrue(cleanup_kwargs["ignore_timeout"])

    @mock.patch.object(telemetry_AFDOGenerate.utils, "run")
    def test_arm_happy_path_runs_perf_inject_before_scp_and_cleanup(
            self, mock_utils_run):
        events = []

        def fake_scp(cmd, timeout=None):
            del timeout
            _assert_valid_bash_syntax(cmd)
            events.append(("scp", cmd))
            return _CmdResult(exit_status=0)

        mock_utils_run.side_effect = fake_scp
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.ArmRun\n")
        orig_run = host.run

        def recording_host_run(cmd, **kwargs):
            events.append(("dut", cmd))
            return orig_run(cmd, **kwargs)

        host.run = recording_host_run
        afdo = _make_test_instance(host, arch="arm")

        with afdo._perf_on_dut():
            self.assertIn("perf-etm.data", host.bg_commands[0])
            # $PPID must remain literal in the outer shell so the inner bash -ec
            # subshell signals perf rather than the SSH session shell.
            self.assertIn(
                    "while kill -USR2 $PPID; do sleep 4; done",
                    host.bg_commands[0],
            )

        cat_idx = next(i for i, (_, cmd) in enumerate(events)
                       if cmd.startswith("cat "))
        inject_idx = next(i for i, (_, cmd) in enumerate(events)
                          if "perf inject" in cmd)
        scp_idx = next(i for i, (kind, _) in enumerate(events)
                       if kind == "scp")
        rm_idx = next(
                i for i, (_, cmd) in enumerate(events)
                if cmd == "rm -rf '/usr/local/tmp/telemetry_afdo.ArmRun'")
        self.assertLess(cat_idx, inject_idx)
        self.assertLess(inject_idx, scp_idx)
        self.assertLess(scp_idx, rm_idx)
        self.assertIn(
                "-i /usr/local/tmp/telemetry_afdo.ArmRun/perf-etm.data "
                "-o /usr/local/tmp/telemetry_afdo.ArmRun/perf.data",
                events[inject_idx][1],
        )

    @mock.patch.object(telemetry_AFDOGenerate.time, "sleep")
    def test_startup_failure_reaps_waits_and_cleans_dir(self, mock_sleep):
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.FailStart\n",
                started_after_polls=None,
                perf_out_stdout="event syntax error\n",
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaisesRegex(RuntimeError, "Perf failed to start"):
            with afdo._perf_on_dut():
                self.fail(
                        "Context body should not run when perf startup fails")

        self.assertEqual(mock_sleep.call_count, 10)
        fg_calls = [(cmd, kwargs) for kind, cmd, kwargs in host.calls
                    if kind == "run"]
        fg_cmds = [cmd for cmd, _ in fg_calls]
        cat_idx = next(i for i, cmd in enumerate(fg_cmds)
                       if cmd.startswith("cat "))
        kill_idx = next(i for i, cmd in enumerate(fg_cmds)
                        if "kill -0 4321 && killall -2 perf" in cmd)
        wait_idx = next(i for i, cmd in enumerate(fg_cmds)
                        if "ps 4321 >/dev/null" in cmd)
        rm_idx = fg_cmds.index(
                "rm -rf '/usr/local/tmp/telemetry_afdo.FailStart'")
        self.assertLess(cat_idx, kill_idx)
        self.assertLess(kill_idx, wait_idx)
        self.assertLess(wait_idx, rm_idx)
        # Inner loop budget (45s) must be strictly below SSH timeout (60s).
        self.assertIn("{1..45}", fg_cmds[wait_idx])
        self.assertEqual(fg_calls[wait_idx][1]["timeout"], 60)
        self.assertTrue(fg_calls[wait_idx][1]["ignore_timeout"])

    @mock.patch.object(telemetry_AFDOGenerate.time, "sleep")
    def test_startup_cat_error_still_reaps_waits_and_cleans_dir(
            self, mock_sleep):
        del mock_sleep
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.NoOut\n",
                started_after_polls=None,
                exit_status_on_substr={"cat ": 1},
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(error.AutoservRunError):
            with afdo._perf_on_dut():
                self.fail("Context body should not run")

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        kill_idx = fg_cmds.index("kill -0 4321 && killall -2 perf")
        wait_idx = next(i for i, cmd in enumerate(fg_cmds)
                        if "ps 4321 >/dev/null" in cmd)
        rm_idx = fg_cmds.index("rm -rf '/usr/local/tmp/telemetry_afdo.NoOut'")
        self.assertLess(kill_idx, wait_idx)
        self.assertLess(wait_idx, rm_idx)

    def test_mid_poll_exception_reaps_waits_and_cleans_dir(self):
        poll_timeout = error.AutoservSSHTimeout("SSH dropped during poll")
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.PollDrop\n",
                raises_on_substr={"test -e ": poll_timeout},
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(error.AutoservSSHTimeout):
            with afdo._perf_on_dut():
                self.fail("Context body should not run")

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        kill_idx = fg_cmds.index("kill -0 4321 && killall -2 perf")
        wait_idx = next(i for i, cmd in enumerate(fg_cmds)
                        if "ps 4321 >/dev/null" in cmd)
        rm_idx = fg_cmds.index(
                "rm -rf '/usr/local/tmp/telemetry_afdo.PollDrop'")
        self.assertLess(kill_idx, wait_idx)
        self.assertLess(wait_idx, rm_idx)

    def test_invalid_perf_pid_reaps_perf_and_cleans_dir(self):
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.BadPid\n",
                bg_pid="not-an-integer\n",
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaisesRegex(RuntimeError,
                                    "Unexpected PID from run_background"):
            with afdo._perf_on_dut():
                self.fail("Context body should not run")

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertIn("killall -2 perf", fg_cmds)
        self.assertEqual(fg_cmds[-1],
                         "rm -rf '/usr/local/tmp/telemetry_afdo.BadPid'")

    def test_post_yield_kill_failure_still_cleans_dir(self):
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.KillFail\n",
                exit_status_on_substr={"kill -0 4321 && killall": 1},
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(RuntimeError):
            with afdo._perf_on_dut():
                pass

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertEqual(fg_cmds[-1],
                         "rm -rf '/usr/local/tmp/telemetry_afdo.KillFail'")

    def test_post_yield_wait_failure_still_cleans_dir(self):
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.WaitFail\n",
                exit_status_on_substr={"ps 4321": 1},
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(RuntimeError):
            with afdo._perf_on_dut():
                pass

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertEqual(fg_cmds[-1],
                         "rm -rf '/usr/local/tmp/telemetry_afdo.WaitFail'")

    def test_arm_perf_inject_failure_still_cleans_dir(self):
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.InjectFail\n",
                exit_status_on_substr={"perf inject": 1},
        )
        afdo = _make_test_instance(host, arch="arm")

        with self.assertRaises(error.AutoservRunError):
            with afdo._perf_on_dut():
                pass

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertEqual(
                fg_cmds[-1],
                "rm -rf '/usr/local/tmp/telemetry_afdo.InjectFail'",
        )

    @mock.patch.object(telemetry_AFDOGenerate.utils, "run")
    def test_scp_failure_still_cleans_dir(self, mock_utils_run):
        mock_utils_run.return_value = _CmdResult(exit_status=1)
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.ScpFail\n")
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(RuntimeError):
            with afdo._perf_on_dut():
                pass

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertEqual(fg_cmds[-1],
                         "rm -rf '/usr/local/tmp/telemetry_afdo.ScpFail'")

    def test_teardown_ssh_timeout_does_not_mask_primary_exception(self):

        class _BenchmarkError(Exception):
            pass

        ssh_timeout = error.AutoservSSHTimeout("SSH timed out during cleanup")
        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.Wedged\n",
                raises_on_substr={
                        "kill -0": ssh_timeout,
                        "ps 4321": ssh_timeout,
                        "rm -rf '/usr/local/tmp/telemetry_afdo.Wedged'":
                        ssh_timeout,
                },
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertLogs(level="WARNING") as captured_logs:
            with self.assertRaises(_BenchmarkError):
                with afdo._perf_on_dut():
                    raise _BenchmarkError("renderer hung during speedometer3")

        fg_calls = [(cmd, kwargs) for kind, cmd, kwargs in host.calls
                    if kind == "run"]
        # Every post-failure command on a wedged DUT must be bounded (60s).
        for cmd, kwargs in fg_calls[2:]:
            self.assertEqual(kwargs["timeout"], 60, msg=cmd)
            self.assertTrue(kwargs["ignore_timeout"], msg=cmd)
        self.assertEqual(
                fg_calls[-1][0],
                "rm -rf '/usr/local/tmp/telemetry_afdo.Wedged'",
        )
        self.assertTrue(
                any("Failed to clean up" in msg
                    for msg in captured_logs.output))

    def test_benchmark_exception_with_dead_perf_does_not_mask_error(self):

        class _BenchmarkError(Exception):
            pass

        host = _FakeHost(
                mktemp_stdout="/usr/local/tmp/telemetry_afdo.Rebooted\n",
                exit_status_on_substr={"kill -0": 1},
        )
        afdo = _make_test_instance(host, arch="amd64")

        with self.assertRaises(_BenchmarkError):
            with afdo._perf_on_dut():
                raise _BenchmarkError("DUT rebooted mid-benchmark")

        fg_cmds = [cmd for kind, cmd, _ in host.calls if kind == "run"]
        self.assertEqual(fg_cmds[-1],
                         "rm -rf '/usr/local/tmp/telemetry_afdo.Rebooted'")

    def test_wait_for_process_handles_none_on_ignore_timeout(self):
        host = mock.Mock()
        host.run.return_value = None
        status = telemetry_AFDOGenerate._wait_for_process(
                host,
                4321,
                45,
                ssh_timeout=60,
                ignore_timeout=True,
        )
        self.assertIsNone(status)
        host.run.assert_called_once()
        _, kwargs = host.run.call_args
        self.assertEqual(kwargs["timeout"], 60)
        self.assertTrue(kwargs["ignore_timeout"])
        self.assertTrue(kwargs["ignore_status"])

    def test_invalid_mktemp_output_raises_without_rm_rf(self):
        for bad_output in (
                "/\n",
                "/usr/local/tmp/telemetry_afdo.\n",
                "/usr/local/tmp/telemetry_afdo.café\n",
                "/usr/local/tmp/telemetry_afdo.bad/sub\n",
        ):
            host = _FakeHost(mktemp_stdout=bad_output)
            afdo = _make_test_instance(host, arch="amd64")

            with self.assertRaisesRegex(RuntimeError,
                                        "Unexpected perf output dir"):
                with afdo._perf_on_dut():
                    self.fail("Context body should not run")

            # Pre-run sweep ran inside mktemp setup, but no post-failure rm -rf
            # may run on the invalid path itself.
            self.assertEqual(len(host.calls), 1)


if __name__ == "__main__":
    unittest.main()

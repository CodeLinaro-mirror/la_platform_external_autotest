# Copyright 2013 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Test to generate the AFDO profile for a set of ChromeOS benchmarks.

This will run a pre-determined set of benchmarks on the DUT under
the monitoring of the linux "perf" tool. The resulting perf.data
file will then be copied to Google Storage (GS) where it can be
used by the AFDO optimized build.

Given that the telemetry benchmarks are quite unstable on ChromeOS at
this point, this test also supports a mode where the benchmarks are
executed outside of the telemetry framework. It is not the same as
executing the benchmarks under telemetry because there is no telemetry
measurement taken but, for the purposes of profiling Chrome, it should
be pretty close.

Example invocation:
/usr/bin/test_that --debug --board=lumpy <DUT IP>
  --args="ignore_failures=True local=True gs_test_location=True"
  telemetry_AFDOGenerate
"""


import bz2
from contextlib import contextmanager
from contextlib import ExitStack
import logging
import os
import time

from autotest_lib.client.common_lib import error
from autotest_lib.server import autotest
from autotest_lib.server import test
from autotest_lib.server import utils
from autotest_lib.server.cros import provision
from autotest_lib.server.cros import filesystem_util
from autotest_lib.server.cros import telemetry_runner
from autotest_lib.server.cros import telemetry_setup
from autotest_lib.site_utils import test_runner_utils

from typing import Optional


# These are arguments to the linux "perf" tool.
# The -e value is processor specific and comes from the Intel SDM vol 3b
INTEL_PROFILER_ARGS = 'record -a -e r20c4 -c 150001 -b'

ARM_PROFILER_ARGS = 'record -e cs_etm/autofdo/u -a -S'
ETM_STROBING_WINDOW = 1000
ETM_STROBING_PERIOD = 10000

# In practice, it takes >2min to copy the perf.data back from the DUT, set
# this timeout to 600 secs to be safe.
WAIT_FOR_CMD_TIMEOUT_SECS = 600

# Time threshold of the test setup in seconds.
# The benchmarks running in this script typically take 60+ seconds.
# Setup time is expected to be 5-10 seconds + extra room.
# - If the test failed and duration was below this limit that would mean that
# the benchmark wasn't started.
# - If the test duration exceeded the setup limit then it is likely the benchmark
# completed but failed in post-processing.
TEST_SETUP_DURATION_LIMIT = 30

RSA_KEY = '-i %s' % test_runner_utils.TEST_KEY_PATH
DUT_SCP_OPTIONS = ' '.join([
        '-o StrictHostKeyChecking=no', '-o UserKnownHostsFile=/dev/null',
        '-o BatchMode=yes', '-o ConnectTimeout=30',
        '-o ServerAliveInterval=900', '-o ServerAliveCountMax=3',
        '-o ConnectionAttempts=4', '-o Protocol=2'
])
# Store perf profiles on stateful (/usr/local/tmp) rather than RAM-backed /tmp
# (multi-GB perf/ETM profiles can exhaust tmpfs) or /usr/local/telemetry
# (not deployed when telemetry_on_dut=False).
_DUT_TMP_DIR = '/usr/local/tmp'
_DUT_PERF_DIR_PREFIX = f'{_DUT_TMP_DIR}/telemetry_afdo.'
_DUT_PERF_DIR_TEMPLATE = f'{_DUT_PERF_DIR_PREFIX}XXXXXX'
_CLEANUP_WAIT_SECS = 45
_CLEANUP_TIMEOUT_SECS = 60

_WAIT_CMD_TEMPLATE = """\
for _ in {1..%(timeout)d}; do \
  ps %(pid)d >/dev/null || break; \
  sleep 1; \
done; \
! ps %(pid)d >/dev/null \
"""


def _wait_for_process(
        host,
        pid,
        timeout,
        ssh_timeout=None,
        ignore_timeout=False,
):
    """Waits for a process on the DUT to terminate.

    Args:
        host: A host object representing the DUT.
        pid: The process ID (integer).
        timeout: Number of seconds to poll for the process to exit.
        ssh_timeout: Optional SSH command timeout in seconds passed to
            `host.run` (`None` uses `host.run`'s default).
        ignore_timeout: Passed to `host.run` to suppress timeout exceptions.

    Returns:
        Exit status integer from the polling command, or `None` if `host.run`
        timed out with `ignore_timeout=True`.
    """
    wait_cmd = _WAIT_CMD_TEMPLATE % {'pid': pid, 'timeout': timeout}
    run_kwargs = {}
    if ssh_timeout is not None:
        run_kwargs['timeout'] = ssh_timeout
    if ignore_timeout:
        run_kwargs['ignore_timeout'] = True
    result = host.run(wait_cmd, ignore_status=True, **run_kwargs)
    return None if result is None else result.exit_status


# List of benchmarks to run to capture profile information. This is
# based on the "superhero" list and other telemetry benchmarks. Goal is
# to have a short list that is as representative as possible and takes a
# short time to execute. At this point the list of benchmarks is in flux.
TELEMETRY_AFDO_BENCHMARKS = (
        {
                'name':
                'rendering.desktop',
                'args': (
                        '--story-tag-filter=motionmark_fixed_2_seconds',
                        '--also-run-disabled-tests',
                        '--assert-gpu-compositing',
                        '--extra-browser-args=--disable-features=SkiaGraphite',
                ),
                'archs': ('amd64', 'arm')
        },
        {
                'name': 'system_health.common_desktop',
                'args': ('--run-abridged-story-set', ),
                'archs': ('amd64', 'arm')
        },
        {
                'name': 'jetstream2',
                'archs': ('amd64', 'arm')
        },
        {
                'name': 'speedometer3',
                'archs': ('amd64', 'arm')
        },
)

# Supported <board>: <architecture>.
LLVM_BOARDS = {
        'chell': 'amd64',
        'hatch': 'amd64',
        'trogdor': 'arm',
}


class telemetry_AFDOGenerate(test.test):
    """Telemetry tests wrapper to collect profiles for AFDO.

    Run one or more telemetry benchmarks under the "perf" monitoring
    tool, generate a "perf.data" file and upload to GS for comsumption
    by the AFDO optimized build.
    """
    version = 1

    def _scp_perf_data(self, dut, perf_dir, host_dir):
        """Copy perf data from dut.

        Args:
            dut: The autotest host object representing DUT.
            perf_dir: The directory on DUT containing perf.data.
            host_dir: The directory on host to put the file.

        Returns:
            Status code for scp command.
        """
        cmd = []
        src = utils.sh_quote_word(f'root@{dut.hostname}:{perf_dir}/perf.data')
        cmd.extend([
                'scp',
                DUT_SCP_OPTIONS,
                RSA_KEY,
                '-P %s' % str(dut.port) if dut.port else '',
                '-v',
                src,
                utils.sh_quote_word(host_dir),
        ])
        command = ' '.join(cmd)

        logging.debug('Retrieving Perf Data: %s', command)
        try:
            result = utils.run(command, timeout=WAIT_FOR_CMD_TIMEOUT_SECS)
            exit_code = result.exit_status
        except Exception as e:
            logging.error('Failed to retrieve results: %s', e)
            raise

        logging.debug('command return value: %d', exit_code)
        return exit_code

    def _run_cleanup_cmd(self, cmd):
        """Runs best-effort DUT cleanup without masking primary errors."""
        # SSHHost.run() still raises AutoservSSHTimeout/CmdTimeoutError under
        # ignore_status=True and defaults timeout=None to 3600s when the DUT
        # crashes or hangs; bound timeout and catch exceptions during teardown.
        try:
            self._host.run(
                    cmd,
                    timeout=_CLEANUP_TIMEOUT_SECS,
                    ignore_status=True,
                    ignore_timeout=True,
            )
        except Exception as e:  # pylint: disable=broad-except
            logging.warning('Failed to clean up on DUT (%s): %s', cmd, e)

    def _reap_perf_best_effort(self, perf_pid):
        """Stops and waits for background perf PID without masking errors."""
        # Wait for the background subshell (`perf_cmd` trailing echo) to exit
        # before `_dut_perf_dir()` runs `rm -rf` to avoid an ENOTEMPTY race.
        # Keep the wait loop budget strictly below `_CLEANUP_TIMEOUT_SECS` so
        # the trailing `! ps` check completes before `host.run` times out.
        self._run_cleanup_cmd(f'kill -0 {perf_pid} && killall -2 perf')
        try:
            _wait_for_process(
                    self._host,
                    perf_pid,
                    _CLEANUP_WAIT_SECS,
                    ssh_timeout=_CLEANUP_TIMEOUT_SECS,
                    ignore_timeout=True,
            )
        except Exception as e:  # pylint: disable=broad-except
            logging.warning('Failed waiting for perf PID %d on DUT: %s',
                            perf_pid, e)

    @contextmanager
    def _dut_perf_dir(self):
        """Creates and cleans up an isolated perf directory on stateful."""
        # Create /usr/local/tmp with mode 1777 if absent so non-root callers
        # have a world-writable stateful tmpdir, stop any orphaned perf process
        # and sweep leftovers from crashed runs whose teardown never ran (safe
        # because `control` schedules one exclusive single-tenant instance per
        # DUT), and create a 0700 per-run directory.
        perf_dir = self._host.run(
                f'mkdir -p -m 1777 {utils.sh_quote_word(_DUT_TMP_DIR)} && '
                '(killall -2 perf || true) && '
                f'rm -rf {utils.sh_quote_word(_DUT_PERF_DIR_PREFIX)}* && '
                f'mktemp -d {utils.sh_quote_word(_DUT_PERF_DIR_TEMPLATE)}',
                verbose=True,
        ).stdout.strip()
        # Require strictly ASCII `[A-Za-z0-9]+` (`str.isalnum()` alone accepts
        # non-ASCII Unicode letters) so unquoted interpolation below is safe.
        suffix = perf_dir[len(_DUT_PERF_DIR_PREFIX):]
        if (not perf_dir.startswith(_DUT_PERF_DIR_PREFIX)
                    or not (suffix.isascii() and suffix.isalnum())):
            raise RuntimeError(
                    f'Unexpected perf output dir from mktemp: {perf_dir!r}')
        try:
            yield perf_dir
        finally:
            # Always delete multi-GB perf/ETM profiles even on scp/perf-inject
            # failure: Swarming DUTs are shared across non-AFDO tests that do
            # not sweep `/usr/local/tmp/telemetry_afdo.*` (b/563185846).
            self._run_cleanup_cmd(f'rm -rf {utils.sh_quote_word(perf_dir)}')

    @contextmanager
    def _perf_on_dut(self):
        """Start and kill perf process on DUT."""
        with self._dut_perf_dir() as perf_dir, self._run_perf_in_dir(perf_dir):
            yield

    @contextmanager
    def _run_perf_in_dir(self, perf_dir):
        """Runs perf profiling using `perf_dir` on the DUT."""
        logging.info('Starting perf process in background.')
        # Note that the below makes use of a supported, but rarely-seen, perf
        # feature. e.g., `perf record -a sleep 5` will record _the entire
        # system_ for 5 seconds.
        #
        # This functionality is used for two reasons:
        # 1. The command can write a file noting that perf has successfully
        #    started and is capable of taking samples. This is required for us
        #    to `yield` from this context manager.
        # 2. On ARM, we need to send -USR2 signals at a regular interval for
        #    profiling to work. These are done inside of this subprocess, since
        #    it leads to simpler code, and creates a two-way liveness link
        #    between perf and the loop:
        #      - if perf is killed, the `sleep; kill -USR2` loop will be
        #        killed.
        #      - if the `sleep; kill -USR2` loop is somehow faulty, perf will
        #        shut down, which is a useful smoke signal.
        if self._is_arm():
            profile_args = ARM_PROFILER_ARGS
            perf_data = 'perf-etm.data'
            sleep_loop = 'while kill -USR2 $PPID; do sleep 4; done'
        else:
            # Any reasonable invocation of this script should take no more than
            # a few hours. Run perf for way longer than that.
            sleep_loop = 'sleep 99h'
            profile_args = INTEL_PROFILER_ARGS
            perf_data = 'perf.data'

        perf_out_file = f'{perf_dir}/perf_output'
        perf_started_file = f'{perf_dir}/perf_started'
        perf_data_file = f'{perf_dir}/{perf_data}'

        profile_command = f'touch {perf_started_file}; {sleep_loop}'
        perf_cmd = (f'nohup perf {profile_args} '
                    f'-o {perf_data_file} '
                    f'>{perf_out_file} 2>&1 '
                    f"bash -ec '{profile_command}'; "
                    f'echo "Perf exited with $?" >> {perf_out_file}')
        raw_pid = self._host.run_background(perf_cmd, verbose=True)
        try:
            perf_pid = int(raw_pid.strip())
        except ValueError as exc:
            self._run_cleanup_cmd('killall -2 perf')
            raise RuntimeError(
                    f'Unexpected PID from run_background: {raw_pid!r}'
            ) from exc

        try:
            # N.B., Number of retries & the sleep multiplier are arbitrarily
            # selected, but should result in waiting about 2mins for perf to
            # start. Existing code didn't wait at all and it _generally_
            # worked, so 2mins should be plenty.
            try_pings = 10
            for i in range(1, try_pings + 1):
                logging.info('Checking if perf has started (try %d/%d)...', i,
                             try_pings)
                test_exit = self._host.run(f'test -e {perf_started_file}',
                                           ignore_status=True).exit_status
                # If `perf_started_file` exists, perf has fully started up.
                if not test_exit:
                    break

                logging.info('Perf not yet started. Sleeping a bit...')
                time.sleep(i * 2)
            else:
                # Don't ignore the status; this command failing is useful
                # signal, and we're going to `raise` in a few lines anyway.
                perf_out = self._host.run(f'cat {perf_out_file}')
                logging.error(
                        'Perf process not started correctly on DUT; stdstreams:'
                        '\n%s',
                        perf_out.stdout,
                )
                raise RuntimeError('Perf failed to start on DUT')
        except BaseException:
            self._reap_perf_best_effort(perf_pid)
            raise

        logging.info('Perf successfully started; PID: %d', perf_pid)
        try:
            yield
        except BaseException:
            # Use bounded best-effort cleanup when the benchmark body fails so
            # a wedged/rebooted DUT cannot block for 3600s or clobber the
            # primary benchmark exception with `AutoservSSHTimeout` or bare
            # `RuntimeError`.
            self._reap_perf_best_effort(perf_pid)
            raise

        # Normal return path: enforce clean `perf` shutdown before harvesting
        # `perf.data`. Check if process is still alive after benchmark run, and
        # if so, stop it with -2 (SIGINT).
        kill_cmd = f'kill -0 {perf_pid} && killall -2 perf'
        if self._host.run(kill_cmd, ignore_status=True).exit_status != 0:
            logging.error('Perf process is not killed correctly on DUT.')
            raise RuntimeError
        # Perf process may not be terminated right after the kill command,
        # wait until perf process finishes.
        status = _wait_for_process(self._host, perf_pid,
                                   WAIT_FOR_CMD_TIMEOUT_SECS)
        if status != 0:
            logging.error('Error waiting for perf process to be killed.')
            raise RuntimeError
        logging.info('Perf has been killed on DUT.')
        perf_out = self._host.run(f'cat {perf_out_file}', ignore_status=True)
        logging.info('Perf output on DUT:\n%s', perf_out.stdout)

        if self._is_arm():
            # Now we need to convert ETM data into Intel's LBR format
            # which allows us to re-use the same AFDO pipeline.
            perf_inject_cmd = ('perf inject --itrace=i1000il --strip '
                               f'-i {perf_dir}/perf-etm.data '
                               f'-o {perf_dir}/perf.data')
            if self._host.run(perf_inject_cmd).exit_status != 0:
                logging.error(
                    'Perf inject failed to convert ETM trace into LBR format.')
                raise RuntimeError

        status = self._scp_perf_data(self._host, perf_dir, self.profdir)
        if status != 0:
            logging.error('Cannot copy perf.data file to host.')
            raise RuntimeError

    @contextmanager
    def _disable_cpuidle(self):
        """Disable CPU idle states in a context. See b/185490945."""
        cpuidle_states = '/sys/devices/system/cpu/cpu*/cpuidle/state*/disable'
        # Disable CPU Idle states to reduce ETM performance overhead.
        disable_cmd = f'echo 1 | tee {cpuidle_states}'
        if self._host.run(disable_cmd).exit_status != 0:
            logging.error('Failed to disable CPU idle states before perf run.')
            raise RuntimeError
        try:
            yield
        finally:
            # Re-enable CPU idle.
            enable_cmd = f'echo 0 | tee {cpuidle_states}'
            if self._host.run(enable_cmd).exit_status != 0:
                logging.error(
                    'Failed to re-enable CPU idle states after perf run.')
                raise RuntimeError

    def _set_strobing(self, window, period):
        """Set ETM strobing settings."""
        stat1 = self._host.run(
            f'echo {window} > /sys/kernel/config/cs-syscfg/features/strobing/'
            'params/window/value')
        stat2 = self._host.run(
            f'echo {period} > /sys/kernel/config/cs-syscfg/features/strobing/'
            'params/period/value')
        if stat1.exit_status != 0 or stat2.exit_status != 0:
            logging.error(
                'Failed to set up ETM strobing settings. '
                'W/o strobing perf profiles can have 100x increase in size.')
            raise RuntimeError

    # TODO(b/328620954): remove this once the deeper issue here is fixed.
    # In particular, see comment #4 on the bug for some rationale/pointers.
    def _inject_host_info_into_host(self, host):
        """Hack to inject info scraped from the device into `host`."""
        # N.B., ignore_status defaults to False, so this is checked by default.
        run_result = self._host.run('cat /etc/lsb-release')
        stdout = run_result.stdout
        logging.info("/etc/lsb-release contents:\n%s", stdout)

        want_key = 'CHROMEOS_RELEASE_BUILDER_PATH'
        want_key_eq = f'{want_key}='
        builder_path_lines = [
                x for x in stdout.splitlines() if x.startswith(want_key_eq)
        ]
        if not builder_path_lines:
            logging.info('No %s found; skip injection.', want_key)
            return None

        if len(builder_path_lines) > 1:
            raise ValueError(
                    f'Want 1 {want_key} line in /etc/lsb-release; got '
                    f'{builder_path_lines}')

        release_builder_path = builder_path_lines[0].split('=', 1)[1]
        logging.info('Detected %s%s', want_key_eq, release_builder_path)

        host_info = host.host_info_store.get()
        host_info.set_version_label(provision.CROS_VERSION_PREFIX,
                                    release_builder_path)
        host.host_info_store.commit(host_info)
        return release_builder_path

    def run_once(self, host, args):
        """Run a set of telemetry benchmarks.

        @param host: Host machine where test is run
        @param args: A dictionary of the arguments that were passed
                to this test.
        @returns None.
        """
        self._host = host
        self._board = host.get_board().split(':')[1]

        self._parse_args(args)

        # Remove write protection on host, as now telemetry code will
        # try to remove write protection that causes the machine to
        # reboot and remount during run_benchmark. We want to avoid it.
        filesystem_util.make_rootfs_writable(self._host)
        builder_path = self._inject_host_info_into_host(host)
        is_staging_run = (builder_path is not None
                          and builder_path.startswith("staging-"))
        self._gs_staging_location = is_staging_run
        setup_bucket_override = (telemetry_setup.STAGING_DEPS_BUCKET
                                 if is_staging_run else None)

        with ExitStack() as stack:
            if self._is_arm():
                self._set_strobing(ETM_STROBING_WINDOW, ETM_STROBING_PERIOD)
                stack.enter_context(self._disable_cpuidle())
            stack.enter_context(self._perf_on_dut())

            if self._minimal_telemetry:
                self._run_tests_minimal_telemetry()
            else:
                tr = stack.enter_context(
                        telemetry_runner.TelemetryRunnerFactory().get_runner(
                                self._host,
                                self._local,
                                telemetry_on_dut=False,
                                override_setup_gs_bucket=setup_bucket_override,
                        ))
                for benchmark_info in TELEMETRY_AFDO_BENCHMARKS:
                    if self._arch not in benchmark_info['archs']:
                        continue
                    benchmark = benchmark_info['name']
                    args = benchmark_info.setdefault('args', [])
                    self._run_test(tr, benchmark, *args)
        self._passed = True

    def after_run_once(self):
        """After the profile information has been collected, compress it
        and upload it to GS
        """
        if not self._passed:
            return

        PERF_FILE = 'perf.data'
        COMP_PERF_FILE = 'chromeos-chrome-{arch}-{ver}.perf.data'
        perf_data = os.path.join(self.profdir, PERF_FILE)
        version_with_suffix = f"{self._version}_p{int(time.time())}"
        comp_data = os.path.join(
                self.profdir,
                COMP_PERF_FILE.format(arch=self._arch,
                                      ver=version_with_suffix))
        compressed = self._compress_file(perf_data, comp_data)
        self._gs_upload(compressed, os.path.basename(compressed))

        # Also create copy of this file using "LATEST" as version so
        # it can be found in case the builder is looking for a version
        # number that does not match. It is ok to use a slighly old
        # version of the this file for the optimized build
        latest_data = COMP_PERF_FILE.format(arch=self._arch, ver='LATEST')
        latest_compressed = self._get_compressed_name(latest_data)
        self._gs_upload(compressed, latest_compressed)

        # So that they are not uploaded along with the logs.
        os.remove(compressed)
        os.remove(perf_data)

    def _parse_args(self, args):
        """Parses and validates input arguments to this autotest.

        @param args: Options->values dictionary.
        @raises error.TestFail if a bad option is passed.
        """
        # Set default values for the options.

        # Architecture for which we are collecting afdo data
        # is based on the board.
        if self._board not in LLVM_BOARDS:
            raise error.TestFail(
                f'This test cannot be run on board {self._board}. '
                f'Try one of {sorted(LLVM_BOARDS)}')
        self._arch = LLVM_BOARDS[self._board]
        # By default, write to production storage. You can upload to a
        # more-accessible (and more temporary) location by passing
        # 'gs_test_location=True'.
        self._gs_test_location = False
        # Ignore individual test failures unless they failed to start.
        self._ignore_failures = True
        # Use local copy of telemetry instead of using the dev server copy.
        self._local = False
        # Chrome version to which the AFDO data corresponds.
        self._version, _ = self._host.get_chrome_version()
        # Try to use the minimal support from Telemetry. The Telemetry
        # benchmarks in ChromeOS are too flaky at this point. So, initially,
        # this will be set to True by default.
        self._minimal_telemetry = False
        # Set when the telemetry test pass.
        self._passed = False
        # Set to True if this should upload to a staging bucket instead of the
        # production one.
        self._gs_staging_location = False

        # Ignored servo arguments.
        ignored_options = (
                'buildartifactsurl',
                'cache_endpoint',
                'dut_servers',
                'is_al_run',
                'is_partner_run',
                'libs_server',
                'servo_host',
                'servo_port',
        )

        unknown_options = []
        for option_name, value in args.items():
            if option_name == 'arch':
                # Verify board: arch.
                if self._arch != value:
                    raise error.TestFail(
                        'Mismatch of the board and architecture: '
                        f'board: {self._board}, arch: {value}. '
                        f'Did you mean "arch={self._arch}"?')
            elif option_name == 'gs_test_location':
                self._gs_test_location = (value == 'True')
            elif option_name == 'ignore_failures':
                self._ignore_failures = (value == 'True')
            elif option_name == 'local':
                self._local = (value == 'True')
            elif option_name == 'minimal_telemetry':
                self._minimal_telemetry = (value == 'True')
            elif option_name == 'version':
                self._version = value
            elif option_name not in ignored_options:
                unknown_options.append(option_name)

        if unknown_options:
            raise error.TestFail(f'Unknown options passed: {unknown_options}')

    def _is_arm(self):
        """Return true if arch is arm."""
        return self._arch == 'arm'

    def _check_status(
            self,
            benchmark: str,
            ex: Optional[error.TestBaseException],
            status: str,
            duration: float,
    ) -> Optional[error.TestBaseException]:
        """Convert benchmark status into the afdo generate exception or None.

        Checks the test status based on return status, duration and
        'ignore_failures option. Log any errors including suppressed.

        @param benchmark: Name.
        @param status: Result status from telemetry_runner.
        @param ex: None, if no exception otherwise TestBaseException.
        @param duration: Test duration.

        @return error.TestFail or None
        """

        cant_ignore_format_message = (
                '"%s" failed after %ds.\n'
                'Can\'t ignore the failure with "ignore_failures" option set'
                ' because the benchmark failed to start.\n'
                'Fix setup or disable the benchmark if the problem persists.\n'
        )
        if ex:
            logging.warning(
                    'Got exception from Telemetry benchmark %s '
                    'after %f seconds. Exception: %s', benchmark, duration, ex)
            if not self._ignore_failures:
                return ex

            # 'ignore_failures' option is set.
            if duration >= TEST_SETUP_DURATION_LIMIT:
                # Ignore failures.
                logging.info('Ignoring failure from benchmark %s.', benchmark)
                return None

            # Premature failure, can't ignore.
            logging.error(cant_ignore_format_message, benchmark, duration)
            return ex
        else:
            logging.info(
                    'Completed Telemetry benchmark %s in %f seconds with status: %s',
                    benchmark,
                    duration,
                    status,
            )

            if status == telemetry_runner.SUCCESS_STATUS:
                return None

            err = error.TestFail(
                    f'An error occurred while executing benchmark: {benchmark}'
            )
            if not self._ignore_failures:
                return err

            # 'ignore_failures' option is set.
            if duration >= TEST_SETUP_DURATION_LIMIT:
                # Benchmark was launched, failed and ignored.
                logging.info(
                        "Ignoring failure with status '%s' returned by %s"
                        " (due to set 'ignore_failures' option)",
                        status,
                        benchmark,
                )
                return None

            # Failed prematurely.
            logging.error(cant_ignore_format_message, benchmark, duration)
            return err

    def _run_test(self, tr, benchmark, *args):
        """Run the benchmark using Telemetry.

        @param tr: Instance of the TelemetryRunner subclass.
        @param benchmark: Name of the benchmark to run.
        @param args: Additional arguments to pass to the telemetry execution
                     script.
        @raises Raises error.TestBaseException if
                - execution of the test failed and "ignore_failures is not set;
                - "ignore_failures" is set but the test duration is below
                  TEST_SETUP_DURATION_LIMIT meaning that the test wasn't started.
        """
        logging.info('Starting Telemetry benchmark %s', benchmark)
        start_time = time.time()
        try:
            result = tr.run_telemetry_benchmark(benchmark, None, *args)
            duration = time.time() - start_time
            err = self._check_status(benchmark, None, result.status, duration)
            if err:
                raise err
        except error.TestBaseException as e:
            duration = time.time() - start_time
            err = self._check_status(benchmark, e,
                                     telemetry_runner.FAILED_STATUS, duration)
            if err:
                raise err

    def _run_tests_minimal_telemetry(self):
        """Run the benchmarks using the minimal support from Telemetry.

        The benchmarks are run using a client side autotest test. This test
        will control Chrome directly using the chrome.Chrome support and it
        will ask Chrome to display the benchmark pages directly instead of
        using the "page sets" and "measurements" support from Telemetry.
        In this way we avoid using Telemetry benchmark support which is not
        stable on ChromeOS yet.
        """
        AFDO_GENERATE_CLIENT_TEST = 'telemetry_AFDOGenerateClient'

        # Execute the client side test.
        client_at = autotest.Autotest(self._host)
        client_at.run_test(AFDO_GENERATE_CLIENT_TEST, args='')

    @staticmethod
    def _get_compressed_name(name):
        """Given a file name, return bz2 compressed name.

        @param name: Name of uncompressed file.
        @returns name of compressed file.
        """
        return name + '.bz2'

    @staticmethod
    def _compress_file(unc_file, com_file):
        """Compresses specified file with bz2.

        @param unc_file: name of file to compress.
        @param com_file: prefix name of compressed file.
        @raises error.TestFail if compression failed
        @returns Name of compressed file.
        """
        dest = ''
        with open(unc_file, 'rb') as inp:
            dest = telemetry_AFDOGenerate._get_compressed_name(com_file)
            with bz2.BZ2File(dest, 'wb') as out:
                for data in inp:
                    out.write(data)
        if not dest or not os.path.isfile(dest):
            raise error.TestFail(f'Could not compress {unc_file}')
        return dest

    def _gs_upload(self, local_file, remote_basename):
        """Uploads file to google storage specific location.

        @param local_file: name of file to upload.
        @param remote_basename: basename of remote file.
        @raises error.TestFail if upload failed.
        @returns nothing.
        """
        GS_LLVM_DEST = ('gs://chromeos-toolchain-artifacts/afdo/unvetted/'
                        f'benchmark/{remote_basename}')
        GS_STAGING_DEST = ('gs://staging-chromeos-toolchain-artifacts/afdo/'
                           f'unvetted/benchmark/{remote_basename}')
        GS_TEST_DEST = ('gs://chromeos-throw-away-bucket/afdo-job/canonicals/'
                        f'{remote_basename}')
        GS_ACL = 'project-private'

        if self._gs_test_location:
            remote_file = GS_TEST_DEST
        elif self._gs_staging_location:
            remote_file = GS_STAGING_DEST
        elif self._board in LLVM_BOARDS:
            remote_file = GS_LLVM_DEST
        else:
            raise error.TestFail(
                f'This test cannot be run on board {self._board}')

        logging.info('About to upload to GS: %s', remote_file)
        if not utils.gs_upload(
                local_file, remote_file, GS_ACL, result_dir=self.resultsdir):
            logging.info('Failed upload to GS: %s', remote_file)
            raise error.TestFail(
                f'Unable to gs upload {local_file} to {remote_file}')

        logging.info('Successfull upload to GS: %s', remote_file)

# Lint as: python3
# -*- coding: utf-8 -*-
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""On-demand deployment of the Telemetry framework onto a DUT.

Historically the Telemetry framework has been baked into every test image: the
chromeos-base/telemetry package installs it into /usr/local/telemetry, which is
part of the stateful partition payload (stateful.zst). That costs ~1.2 GiB of
stateful space on every DUT, including the large majority of DUTs that never run
an on-DUT Telemetry test:

  - Tast never uses on-DUT Telemetry.
  - Server-side Telemetry tests with telemetry_on_dut=False run the harness on
    the drone and drive the DUT with --browser=cros-chrome --remote=<dut>.

Only two consumers actually need the framework present on the DUT itself:

  A. Server-side tests with telemetry_on_dut=True, which ssh in and run
     /usr/local/telemetry/src/tools/perf/run_benchmark.
  B. Client-side autotest tests that import telemetry.* at module scope (most
     commonly via client/common_lib/cros/chrome.py), resolved through
     telemetry.pth in Python site-packages.

This module provides the deployment primitive those consumers use so that the
framework can eventually be dropped from the image entirely. It fetches the
per-build dep tarball that the Chrome ebuild already publishes inside
autotest_packages.tar and extracts it into /usr/local/telemetry.

Only consumer A is wired up alongside this module; consumer B is served by
server/cros/telemetry_dependency.py, which lands separately.

Note that ensure_telemetry_on_dut() is a no-op on images that still ship
Telemetry, which is what makes it safe to land and bake before the image change.

Every failure raised out of this module is a TelemetryDeployError; _try_run() is
the single place where a host exception is converted into one, so callers can
attribute failures without listing autotest's exception hierarchy.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import os
import time

import common
from autotest_lib.client.common_lib import global_config
from autotest_lib.client.common_lib import packages
from autotest_lib.client.common_lib import utils
from autotest_lib.client.common_lib.cros import dev_server
from autotest_lib.server.cros import telemetry_setup

# Where the Telemetry framework lives on the DUT. /usr/local is a bind mount of
# /mnt/stateful_partition/dev_image, so this is on the stateful partition.
#
# TELEMETRY_DUT_SRC is the canonical spelling of the directory the harness execs
# out of; server/cros/telemetry_runner.py aliases DUT_CHROME_ROOT to it rather
# than repeating the literal, so a deploy and the benchmark that follows it
# cannot disagree about where the tree is.
TELEMETRY_DUT_DIR = '/usr/local/telemetry'
TELEMETRY_DUT_SRC = os.path.join(TELEMETRY_DUT_DIR, 'src')
_TELEMETRY_REL_PYTHON_PATH = 'third_party/catapult/telemetry'
_TELEMETRY_PYTHON_PATH = os.path.join(TELEMETRY_DUT_SRC,
                                      _TELEMETRY_REL_PYTHON_PATH)
_USR_PREFIX = '/usr'
_SITE_PACKAGES_REL_GLOB = 'lib*/python3.*/site-packages'
_PTH_FILENAME = 'telemetry.pth'

# Scratch locations, deliberately on the same filesystem as TELEMETRY_DUT_DIR so
# that the final move into place is a rename rather than a copy.
_STAGING_DIR = TELEMETRY_DUT_DIR + '.staging'

# Where the compressed payload is downloaded. Deliberately not /tmp, which is a
# small tmpfs on ChromeOS, and a named subdirectory rather than the partition
# root so that anything this module leaks is obvious and removable in one go.
_DUT_FILESYSTEM = '/mnt/stateful_partition'
_SCRATCH_DIR = os.path.join(_DUT_FILESYSTEM, 'telemetry_deploy')

# Temporary staging paths this module creates, in the order they are removed.
_SCRATCH_PATHS = (_SCRATCH_DIR, _STAGING_DIR)

# The dep tarball published per-build by chromeos-base/chromeos-chrome, via
# install_telemetry_dep_resources(). It lives inside autotest_packages.tar at
# autotest/packages/<name> and is not available as a standalone GCS object, so
# it has to be fetched through the cache server's extract endpoint.
#
# They are tried in the order returned by PackageManager.get_tarball_names(),
# which puts packages.DEFAULT_TARBALL_SUFFIX first, and the first one that is
# published for the build wins. Older builds from before the zstd switch are
# reached via the bzip2 fallback probe.
#
# _decompress_flag() derives the tar flag from whichever name resolved, so
# adding or reordering formats needs no other change.
TELEMETRY_DEP_TARBALLS = tuple(
        packages.PackageManager.get_tarball_names('telemetry_dep', 'dep'))

# Everything in the tarball is rooted at test_src/, but Telemetry has paths
# hardcoded to src/, so the tree is renamed after extraction. This mirrors what
# telemetry_setup.TelemetrySetup.Setup() does on the drone, and what the
# telemetry ebuild does at image build time.
_TARBALL_ROOT_DIR = 'test_src'

# Paths (relative to a Telemetry src/ directory) that must exist for a tree to
# count as a usable installation. Checking these rather than just the top-level
# directory means a partially deleted or partially extracted tree is detected
# and repaired instead of being silently trusted.
_SENTINEL_PATHS = (
        _TELEMETRY_REL_PYTHON_PATH,
        'tools/perf/run_benchmark',
)

# Unused test fixtures and corpora excluded when unpacking dep-telemetry_dep:
#   - 'testing' and 'third_party/catapult/tracing/test_data' (~420 MiB) match
#     what chromeos-base/telemetry deleted in src_unpack() at image build time.
#   - 'tools/perf/testdata/task_output_dir' (~48 MiB) holds unit-test fixtures
#     for tools/perf/process_perf_results_unittest.py that are never read at
#     runtime (pruned in chromeos-chrome on newer builds and excluded here for
#     older tarballs).
# Note: 'third_party/catapult/third_party/gsutil' must NOT be pruned here;
# Catapult's py_utils/cloud_storage.py falls back to that vendored gsutil on
# ChromeOS DUTs where depot_tools is not installed.
#
# Relative to a Telemetry src/ directory, like _SENTINEL_PATHS, and verified
# absent after extraction: a pattern that silently stops matching would
# otherwise waste hundreds of MiB on stateful with nothing to show for it.
_PRUNED_PATHS = (
        'testing',
        'third_party/catapult/tracing/test_data',
        'tools/perf/testdata/task_output_dir',
)

# The same paths as tar sees them, before the rename to src/. GNU tar excludes
# the whole subtree under a matching directory, and matching is unanchored, so
# these work whether the archive stores members as 'test_src/...' or
# './test_src/...'.
_EXCLUDED_PATHS = tuple('%s/%s' % (_TARBALL_ROOT_DIR, path)
                        for path in _PRUNED_PATHS)

# Room for the compressed payload on its own. Checked separately from
# _MIN_FREE_BYTES because the download happens before the existing tree is
# removed, so it cannot spend the space that removal will free. Measured on
# R156-16825.0.0: 658 MiB for bzip2, 414 MiB for zstd.
_PAYLOAD_BYTES = int(0.75 * 1024**3)

# Room for the payload and the extracted tree at once, since the tarball is not
# removed until extraction has finished. Measured on R156-16825.0.0: 658 MiB
# compressed plus 1.2 GiB extracted, so ~1.9 GiB, with the rest as slack.
#
# Both numbers grow with Chrome, so this is a floor that has to be revisited,
# not a law. Re-measure if deploys start failing this check on boards that have
# not changed.
_MIN_FREE_BYTES = int(2.5 * 1024**3)

# Generous, because this can involve a ~658 MiB download over the lab network
# followed by a multi-minute decompression on a slow DUT.
_DOWNLOAD_TIMEOUT_SECONDS = 600
_EXTRACT_TIMEOUT_SECONDS = 900
_QUICK_CMD_TIMEOUT_SECONDS = 120

# Deadline curl enforces on itself, comfortably inside the ssh timeout above.
# Without it, repeated --retry cycles of a stalling transfer can run out the ssh
# budget instead, and the failure is reported as a generic command timeout
# rather than as curl's much more specific diagnosis.
_CURL_MAX_TIME_SECONDS = _DOWNLOAD_TIMEOUT_SECONDS - 60

_ENABLE_CONFIG_KEY = 'enable_on_demand_telemetry_deploy'


class TelemetryDeployError(Exception):
    """Raised when Telemetry cannot be deployed to the DUT."""


class _PayloadNotFound(TelemetryDeployError):
    """Raised when the cache server has no such payload for this build.

    Distinct from a failed download so that the next candidate format can be
    tried, rather than papering over a network problem by asking for a
    different file.
    """


def _quoted(*words):
    """Shell-quotes each word and joins them with spaces.

    Every command in this module runs as root on the DUT, and some operands are
    derived from caller-supplied data (the build label reaches the curl command
    by way of the URL), so operands are quoted uniformly rather than only where
    it currently matters.

    @param words: Words to quote.

    @returns A single space-separated string of quoted words.
    """
    return ' '.join(utils.sh_quote_word(word) for word in words)


def _deploy_enabled():
    """Returns whether on-demand deployment is enabled.

    This is a kill switch: if the new behaviour misbehaves in the field it can
    be turned off with a config flip rather than a rollback.

    @returns True if on-demand deployment should be attempted.
    """
    return global_config.global_config.get_config_value('CROS',
                                                        _ENABLE_CONFIG_KEY,
                                                        type=bool,
                                                        default=True)


def _decompress_flag(tarball_name):
    """Returns the tar flag needed to decompress the given tarball.

    Note that --zstd requires tar 1.31 or newer on the DUT and shells out to the
    zstd binary, the way the telemetry ebuild needs BDEPEND="app-arch/zstd" for
    the same reason. ChromeOS test images ship /usr/bin/zstd, so both hold.

    @param tarball_name: File name of the tarball.

    @returns The tar command-line flag to use, e.g. '-j', or '' for plain tar.

    @raises TelemetryDeployError: If the compression format is unrecognized.
    """
    if tarball_name.endswith('.tar.bz2'):
        return '-j'
    if tarball_name.endswith('.tar.zst'):
        return '--zstd'
    if tarball_name.endswith('.tar.gz'):
        return '-z'
    if tarball_name.endswith('.tar'):
        return ''
    raise TelemetryDeployError('Unrecognized tarball format: %s' %
                               tarball_name)


def _try_run(host, cmd, timeout, description):
    """Runs a command on the DUT and reports the result without judging it.

    The single place a host exception becomes a TelemetryDeployError, so that
    callers of this module only ever have to catch the one type.

    @param host: Autotest host object for the DUT.
    @param cmd: Command string to run.
    @param timeout: Timeout in seconds.
    @param description: Human-readable description used in the error message.

    @returns The CmdResult, or None if the command timed out.

    @raises TelemetryDeployError: If the command could not be run at all.
    """
    try:
        return host.run(cmd,
                        timeout=timeout,
                        ignore_status=True,
                        ignore_timeout=True)
    except Exception as e:
        raise TelemetryDeployError('%s could not be run on %s: %s' %
                                   (description, host.hostname, e)) from e


def _check_result(host, result, timeout, description):
    """Turns a non-zero exit or a timeout into a useful error.

    @param host: Autotest host object for the DUT.
    @param result: CmdResult from _try_run, or None.
    @param timeout: Timeout the command was given, for the message.
    @param description: Human-readable description used in the error message.

    @returns The CmdResult.

    @raises TelemetryDeployError: If the command failed or timed out.
    """
    if result is None:
        raise TelemetryDeployError('%s timed out after %ss on %s.' %
                                   (description, timeout, host.hostname))
    if result.exit_status != 0:
        raise TelemetryDeployError(
                '%s failed on %s (exit status %s).\nstderr:\n%s' %
                (description, host.hostname, result.exit_status,
                 result.stderr.strip()))
    return result


def _run(host, cmd, timeout, description):
    """Runs a command on the DUT, raising a useful error on failure.

    @param host: Autotest host object for the DUT.
    @param cmd: Command string to run.
    @param timeout: Timeout in seconds.
    @param description: Human-readable description used in the error message.

    @returns The CmdResult.

    @raises TelemetryDeployError: If the command fails or times out.
    """
    return _check_result(host, _try_run(host, cmd, timeout, description),
                         timeout, description)


def _run_best_effort(host, cmd, timeout, description):
    """Runs a cleanup command on the DUT, swallowing every failure.

    Cleanup runs on paths where something has already gone wrong, and at the end
    of tests where the DUT may have gone away entirely. A failure here must
    never replace the error that is actually worth reporting, so non-zero exits
    and timeouts are ignored and an unreachable DUT is logged rather than
    raised.

    @param host: Autotest host object for the DUT.
    @param cmd: Command string to run.
    @param timeout: Timeout in seconds.
    @param description: Human-readable description used in the log message.
    """
    try:
        _try_run(host, cmd, timeout, description)
    except TelemetryDeployError as e:
        logging.warning('%s failed, continuing anyway: %s', description, e)


def _paths_state(host, base, paths, present):
    """Reports whether the given paths are in the expected state.

    @param host: Autotest host object for the DUT.
    @param base: Directory the paths are relative to.
    @param paths: Paths to test, relative to base.
    @param present: True to test that every path exists, False that none does.

    @returns True if every path is in the expected state, False if any is not,
             or None if the DUT could not be asked.
    """
    operator = '-e' if present else '! -e'
    tests = ' && '.join('test %s %s' %
                        (operator, _quoted(os.path.join(base, path)))
                        for path in paths)
    try:
        result = _try_run(host, tests, _QUICK_CMD_TIMEOUT_SECONDS,
                          'Checking for %s' % base)
    except TelemetryDeployError as e:
        logging.warning('%s', e)
        return None
    return None if result is None else result.exit_status == 0


def is_telemetry_on_dut(host, rootfs_prefix=_USR_PREFIX):
    """Returns whether a usable Telemetry tree and .pth are on the DUT.

    An unreachable DUT counts as "not present": the deploy that follows will
    fail against the same DUT and report the connection problem properly, which
    is a better diagnosis than one raised from a presence check.

    Args:
        host: Autotest host object for the DUT.
        rootfs_prefix: Prefix containing rootfs lib*/python3.*/site-packages
            directories (overridden in unit tests to run against a temp dir).

    Returns:
        True if both the Telemetry sentinel files and a matching rootfs or
        stateful telemetry.pth exist on the DUT.
    """
    prefix = rootfs_prefix.rstrip('/')
    if not prefix:
        return False
    sentinel_checks = ' && '.join(
            'test -e %s' % _quoted(os.path.join(TELEMETRY_DUT_SRC, path))
            for path in _SENTINEL_PATHS)
    # Group the .pth loop in braces after '&&' so missing sentinels
    # short-circuit cleanly on images that ship rootfs telemetry.pth without
    # the tree, while catching trees stranded without .pth across drone skew.
    # Require telemetry.pth in at least one matching site-packages dir (rather
    # than every dir) because ChromeOS Python 3.11 uses --with-platlibdir=lib
    # and chromeos-base/telemetry only writes /usr/lib/python3.*/site-packages,
    # whereas another ebuild may also create /usr/lib64/python3.*/site-packages.
    cmd = ('%(sentinels)s && { '
           'prefix=%(prefix)s; '
           'found=0; '
           'for d in "$prefix"/%(rel_glob)s; do '
           '[ -d "$d" ] || continue; '
           'if [ -f "$d"/%(pth)s ] || '
           '[ -f "$prefix/local/${d#"$prefix"/}"/%(pth)s ]; then '
           'found=1; break; '
           'fi; '
           'done; '
           '[ "$found" -eq 1 ]; '
           '}' % {
                   'sentinels': sentinel_checks,
                   'prefix': _quoted(prefix),
                   'rel_glob': _SITE_PACKAGES_REL_GLOB,
                   'pth': _quoted(_PTH_FILENAME),
           })
    try:
        result = _try_run(host, cmd, _QUICK_CMD_TIMEOUT_SECONDS,
                          'Checking for %s' % TELEMETRY_DUT_SRC)
    except TelemetryDeployError as e:
        logging.warning('%s', e)
        return False
    return result is not None and result.exit_status == 0


def _resolve_build(host, build):
    """Determines the build to fetch the Telemetry dep for.

    @param host: Autotest host object for the DUT.
    @param build: Explicit build (e.g. 'octopus-release/R156-16825.0.0'), or
                  None to read it from the host info store.

    @returns The build string.

    @raises TelemetryDeployError: If the build cannot be determined.
    """
    if build:
        return build

    info = host.host_info_store.get()
    if not info.build:
        raise TelemetryDeployError(
                'Unable to determine the build for host %s, so the Telemetry '
                'dependency cannot be fetched.' % host.hostname)
    return info.build


def _dep_url(host, build, cache_endpoint, bucket, tarball):
    """Builds the cache server URL for the Telemetry dep tarball.

    The dep is only reachable through the cache server's extract endpoint,
    which pulls the single file out of the build's autotest_packages.tar.

    @param host: Autotest host object for the DUT.
    @param build: Build to fetch the dep for.
    @param cache_endpoint: Cache server endpoint, with or without a scheme. In
                           CFT this is resolved outside of tauto and passed in;
                           when None, tauto resolves the devserver itself.
    @param bucket: GS bucket name to fetch from, without a gs:// prefix. Must
                   match the bucket the drone-side tree came from, or the two
                   harnesses can end up on different builds.
    @param tarball: File name of the dep tarball within autotest_packages.tar.

    @returns The URL to download the tarball from.

    @raises TelemetryDeployError: If no cache server can be resolved.
    """
    if cache_endpoint:
        # CFT hands the endpoint down from an env var that may or may not carry
        # a scheme, the same way dev_server.get_available_devservers() does.
        if not cache_endpoint.startswith(('http://', 'https://')):
            cache_endpoint = 'http://%s' % cache_endpoint
        server = dev_server.ImageServer(cache_endpoint)
    else:
        # These are plain Exceptions, so without this they would escape
        # ensure_telemetry_on_dut() unwrapped and reach the runner as an
        # unattributed failure. DevServerOverloadException is deliberately not
        # here: it comes from the async staging RPC, which resolve() does not
        # use.
        try:
            server = dev_server.ImageServer.resolve(build,
                                                    hostname=host.hostname)
        except (dev_server.DevServerException,
                dev_server.DevServerFailToLocateException) as e:
            raise TelemetryDeployError(
                    'Unable to resolve a cache server for build %s on behalf '
                    'of %s: %s' % (build, host.hostname, e))

    return (telemetry_setup.STATIC_URL_TEMPLATE %
            (server.url(), bucket, build, tarball))


def _kilobytes_from(output, column):
    """Pulls a kilobyte count out of the last line of df or du output.

    @param output: Command stdout.
    @param column: Zero-based index of the field holding the count.

    @returns The count in kilobytes, or None if the output is unrecognizable.
    """
    try:
        return int(output.strip().splitlines()[-1].split()[column])
    except (AttributeError, IndexError, ValueError):
        return None


def _free_bytes(host):
    """Returns the free space on the DUT's stateful partition.

    @param host: Autotest host object for the DUT.

    @returns Free space in bytes, or None if it could not be determined.
    """
    try:
        result = _try_run(host, 'df -Pk %s' % _quoted(_DUT_FILESYSTEM),
                          _QUICK_CMD_TIMEOUT_SECONDS, 'Measuring free space')
    except TelemetryDeployError as e:
        logging.warning('%s', e)
        return None
    # df exits non-zero for a path it cannot stat, so a failed run is just
    # another way of not knowing, not a reason to fail the deploy.
    if result is None or result.exit_status != 0:
        return None
    kilobytes = _kilobytes_from(result.stdout, column=3)
    return None if kilobytes is None else kilobytes * 1024


def _reclaimable_bytes(host, path):
    """Returns the space that removing the given path would free.

    Distinguishes "there is nothing there" from "I could not tell". du exits
    non-zero for a path that does not exist, which really does mean nothing can
    be reclaimed; a timeout on a cold multi-gigabyte tree means no such thing,
    and treating it as zero would fail deploys on DUTs with ample room.

    @param host: Autotest host object for the DUT.
    @param path: Path to measure.

    @returns Disk usage in bytes, 0 if the path is absent, or None if it could
             not be determined.
    """
    try:
        result = _try_run(host, 'du -sk %s' % _quoted(path),
                          _QUICK_CMD_TIMEOUT_SECONDS,
                          'Measuring the existing Telemetry tree')
    except TelemetryDeployError as e:
        logging.warning('%s', e)
        return None
    if result is None:
        return None
    if result.exit_status != 0:
        return 0
    kilobytes = _kilobytes_from(result.stdout, column=0)
    return None if kilobytes is None else kilobytes * 1024


def _check_free_space(host):
    """Fails early if the DUT does not have room for the payload.

    Runs before anything is downloaded or removed, so that a DUT which cannot
    fit the payload is reported up front instead of failing part-way through a
    multi-minute extraction, by which point the old tree is already gone.

    Two separate constraints, because they apply at different moments. The
    download runs before the old tree is removed, so it can only use what is
    free right now. The extraction runs after, so it can also use whatever that
    removal frees.

    Anything unmeasurable is treated as a reason to proceed rather than to fail:
    a deploy that might work beats a deploy refused over a df that could not be
    read.

    @param host: Autotest host object for the DUT.

    @raises TelemetryDeployError: If free space is known and insufficient.
    """
    available = _free_bytes(host)
    if available is None:
        logging.warning(
                'Could not determine free space on %s, skipping the free '
                'space check.', host.hostname)
        return

    if available < _PAYLOAD_BYTES:
        raise TelemetryDeployError(
                'Downloading the Telemetry payload to %s needs about %.1f GiB '
                'free on %s but only %.1f GiB is available. The download runs '
                'before the existing tree is removed, so that removal cannot '
                'make room for it. Free space on the DUT, or use a board with '
                'a larger stateful partition.' %
                (host.hostname, _PAYLOAD_BYTES / 1024**3, _DUT_FILESYSTEM,
                 available / 1024**3))

    reclaimable = _reclaimable_bytes(host, TELEMETRY_DUT_DIR)
    if reclaimable is None:
        logging.warning(
                'Could not measure the existing Telemetry tree on %s, '
                'skipping the rest of the free space check.', host.hostname)
        return

    effective = available + reclaimable
    logging.info(
            '%s has %.1f GiB free on %s, plus %.1f GiB reclaimable from the '
            'existing Telemetry tree.', host.hostname, available / 1024**3,
            _DUT_FILESYSTEM, reclaimable / 1024**3)

    if effective < _MIN_FREE_BYTES:
        raise TelemetryDeployError(
                'Deploying Telemetry to %s needs about %.1f GiB free on %s but '
                'only %.1f GiB is or can be made available. Free space on the '
                'DUT, or use a board with a larger stateful partition.' %
                (host.hostname, _MIN_FREE_BYTES / 1024**3, _DUT_FILESYSTEM,
                 effective / 1024**3))


def _download_one(host, url, dest, tarball):
    """Downloads a single candidate payload onto the DUT.

    @param host: Autotest host object for the DUT.
    @param url: URL to download.
    @param dest: Path to download to on the DUT.
    @param tarball: File name being fetched, for the error messages.

    @raises _PayloadNotFound: If the cache server answered with HTTP 404 or 410.
    @raises TelemetryDeployError: If the download failed for any other reason.
    """
    # --fail turns an HTTP error into a non-zero exit instead of a saved error
    # page. --retry covers a transient blip on the lab network, and the speed
    # limit aborts a stalled transfer rather than letting it consume the whole
    # timeout budget.
    #
    # --proto and --max-redirs bound where a redirect can send a DUT that
    # unpacks the result as root. The cache server is plain HTTP today and
    # redirects are followed, so this is defence in depth rather than a fix.
    description = 'Downloading %s from the cache server' % tarball
    cmd = ('curl --fail --location --silent --show-error '
           "--proto '=http,https' --max-redirs 3 "
           '--retry 3 --retry-connrefused '
           '--speed-limit 1024 --speed-time 60 '
           '--max-time %d '
           "-w '%%{http_code}' "
           '--output %s %s' %
           (_CURL_MAX_TIME_SECONDS, _quoted(dest), _quoted(url)))

    result = _try_run(host, cmd, _DOWNLOAD_TIMEOUT_SECONDS, description)
    if (
            result is not None
            and packages.is_http_not_found(result.exit_status, result.stdout)):
        raise _PayloadNotFound('%s is not published for this build.' % tarball)
    _check_result(host, result, _DOWNLOAD_TIMEOUT_SECONDS, description)
    logging.info('Downloaded Telemetry payload %s from %s.', tarball, url)


def _download_payload(host, build, cache_endpoint, bucket):
    """Downloads whichever payload format this build publishes.

    @param host: Autotest host object for the DUT.
    @param build: Build to fetch the dep for.
    @param cache_endpoint: Cache server endpoint, or None to resolve one.
    @param bucket: GS bucket name to fetch from.

    @returns A (path on the DUT, tarball name) pair.

    @raises TelemetryDeployError: If no candidate could be downloaded.
    """
    _run(host,
         'mkdir -p %s' % _quoted(_SCRATCH_DIR),
         timeout=_QUICK_CMD_TIMEOUT_SECONDS,
         description='Creating the Telemetry download directory')

    missing = []
    for tarball in TELEMETRY_DEP_TARBALLS:
        url = _dep_url(host, build, cache_endpoint, bucket, tarball)
        dest = os.path.join(_SCRATCH_DIR, tarball)
        try:
            _download_one(host, url, dest, tarball)
            return dest, tarball
        except _PayloadNotFound as e:
            logging.info('%s Trying the next format.', e)
            missing.append(tarball)

    raise TelemetryDeployError(
            'The cache server has no Telemetry payload for build %s: none of '
            '%s is published inside its autotest_packages.tar. Either the '
            'build did not produce one, or it publishes a compression format '
            'this module does not know about.' % (build, list(missing)))


def _extract_on_dut(host, tarball_path, tarball):
    """Extracts the tarball into a staging directory and verifies it.

    @param host: Autotest host object for the DUT.
    @param tarball_path: Path to the tarball on the DUT.
    @param tarball: File name of the tarball, which determines its format.

    @raises TelemetryDeployError: If extraction or verification fails.
    """
    _run(host,
         'rm -rf %(dir)s && mkdir -p %(dir)s' % {'dir': _quoted(_STAGING_DIR)},
         timeout=_QUICK_CMD_TIMEOUT_SECONDS,
         description='Preparing the Telemetry staging directory')

    # --no-same-owner because extraction runs as root, which would otherwise
    # restore whatever ownership the build machine recorded in the archive.
    # Built as a list so that a format with no flag does not leave a stray
    # empty argument behind. Everything appended here is either a literal flag
    # or has been through _quoted(); nothing raw goes in.
    args = ['tar', 'x', '--no-same-owner']
    flag = _decompress_flag(tarball)
    if flag:
        args.append(flag)
    args += ['--exclude=%s' % _quoted(path) for path in _EXCLUDED_PATHS]
    args += ['-f', _quoted(tarball_path), '-C', _quoted(_STAGING_DIR)]
    logging.info('Extracting Telemetry payload %s (flag: %s) on %s.', tarball,
                 flag or 'none', host.hostname)
    _run(host,
         ' '.join(args),
         timeout=_EXTRACT_TIMEOUT_SECONDS,
         description='Extracting %s' % tarball)

    staged_src = os.path.join(_STAGING_DIR, 'src')
    _run(host,
         'mv %s' %
         _quoted(os.path.join(_STAGING_DIR, _TARBALL_ROOT_DIR), staged_src),
         timeout=_QUICK_CMD_TIMEOUT_SECONDS,
         description='Renaming %s to src' % _TARBALL_ROOT_DIR)

    _verify_staged_tree(host, staged_src, tarball)


def _verify_staged_tree(host, staged_src, tarball):
    """Checks that the staged tree is complete and correctly pruned.

    Both halves matter. A missing sentinel means the tree is unusable. A pruned
    directory that is present means an --exclude pattern stopped matching, which
    costs ~420 MiB with nothing to show for it and which nothing else would ever
    notice.

    @param host: Autotest host object for the DUT.
    @param staged_src: Path to the staged Telemetry src/ directory.
    @param tarball: File name the tree came from, for the error messages.

    @raises TelemetryDeployError: If the tree is unusable or unverifiable.
    """
    for paths, present, problem in (
            (_SENTINEL_PATHS, True, 'one of %s is missing'),
            (_PRUNED_PATHS, False,
             'one of %s should have been excluded but is '
             'present'),
    ):
        state = _paths_state(host, staged_src, paths, present=present)
        if state is None:
            raise TelemetryDeployError(
                    'Telemetry was extracted on %s but the staged tree under '
                    '%s could not be checked, so it cannot be published.' %
                    (host.hostname, staged_src))
        if not state:
            raise TelemetryDeployError(
                    ('Telemetry was extracted on %s but ' + problem +
                     ' under %s. The %s payload for this build may be corrupt '
                     'or may have changed layout.') %
                    (host.hostname, list(paths), staged_src, tarball))


def _remove_existing_tree(host):
    """Deletes any Telemetry tree already on the DUT.

    Has to happen before extraction rather than after: the partition does not
    have room for the old tree, the tarball and the new tree at once. Reaching
    this point means the old tree is either unusable (it failed the sentinel
    check) or is being deliberately replaced, so nothing worth keeping is lost.

    Args:
        host: Autotest host object for the DUT.
    """
    _run(host,
         'rm -rf %s' % _quoted(TELEMETRY_DUT_DIR),
         timeout=_EXTRACT_TIMEOUT_SECONDS,
         description='Removing any existing Telemetry tree')


def _swap_into_place(host):
    """Moves the staged tree to TELEMETRY_DUT_DIR.

    _remove_existing_tree() has already run, so this is a rename onto a path
    that does not exist. The rm is there for the case where something recreated
    it in between, where letting mv nest the staged tree inside it would be much
    harder to diagnose than replacing it.

    This says nothing about two deploys racing each other: the scratch paths are
    fixed, so concurrent deploys against one DUT would clobber each other. DUTs
    are held exclusively for the duration of a test, so that does not arise in
    practice.

    Args:
        host: Autotest host object for the DUT.
    """
    _run(host,
         'rm -rf %s && mv %s' % (_quoted(TELEMETRY_DUT_DIR),
                                 _quoted(_STAGING_DIR, TELEMETRY_DUT_DIR)),
         timeout=_EXTRACT_TIMEOUT_SECONDS,
         description='Moving the staged Telemetry tree into place')


def _ensure_python_path(host, rootfs_prefix=_USR_PREFIX):
    """Ensures site-packages under stateful contains telemetry.pth.

    ChromeOS Python's site.getsitepackages() derives lib/ and sys.platlibdir
    candidates under every entry in PREFIXES (which includes /usr/local) and
    processes any candidate directory that exists on disk. On ChromeOS test
    images, rootfs /usr/lib/python3.* is a symlink to /usr/local/usr/lib/python3.*
    and /usr/local/usr is a symlink to '.', so /usr/lib/python3.*/site-packages
    and /usr/local/lib/python3.*/site-packages resolve to the same directory on
    stateful. Writing telemetry.pth under $prefix/local/${d#"$prefix"/}
    (matching chromeos-base/telemetry's install_python()) keeps client imports
    working on stateful without modifying the read-only rootfs, even after
    chromeos-base/telemetry is removed completely.

    Args:
        host: Autotest host object for the DUT.
        rootfs_prefix: Prefix containing rootfs lib*/python3.*/site-packages
            directories (overridden in unit tests to run against a temp dir).

    Raises:
        TelemetryDeployError: If no site-packages directory matched or writing
            telemetry.pth failed.
    """
    prefix = rootfs_prefix.rstrip('/')
    if not prefix:
        raise TelemetryDeployError('Invalid empty rootfs_prefix: %r' %
                                   rootfs_prefix)
    # Enforce world-readable permissions (umask 022) for non-root client
    # processes, clear any directory occupying the destination path so mv -f
    # cannot nest inside it, write via a temp file so symlinks on stateful are
    # never followed as root, and fail if no site-packages directory matched.
    # On --with-platlibdir=lib builds that also have a stray /usr/lib64
    # site-packages directory, this also writes a lib64 copy that site.py never
    # reads, which is harmless and avoids coupling to sys.platlibdir.
    cmd = ('umask 022; '
           'prefix=%(prefix)s; '
           'written=0; '
           'for d in "$prefix"/%(rel_glob)s; do '
           '[ -d "$d" ] || continue; '
           'local_dir="$prefix/local/${d#"$prefix"/}"; '
           'tmp_pth="$local_dir/.%(pth_raw)s.$$"; '
           'if ! { mkdir -p "$local_dir" && '
           '{ [ ! -d "$local_dir"/%(pth)s ] || rm -rf "$local_dir"/%(pth)s; }'
           ' && rm -f "$tmp_pth" && '
           "printf '%%s\\n' %(path)s > \"$tmp_pth\" && "
           'mv -f "$tmp_pth" "$local_dir"/%(pth)s; }; then '
           'rm -f "$tmp_pth"; '
           'exit 1; '
           'fi; '
           'written=$((written + 1)); '
           'done; '
           'if [ "$written" -eq 0 ]; then '
           'echo "No directory matched $prefix/%(rel_glob)s" >&2; '
           'exit 1; '
           'fi' % {
                   'prefix': _quoted(prefix),
                   'rel_glob': _SITE_PACKAGES_REL_GLOB,
                   'path': _quoted(_TELEMETRY_PYTHON_PATH),
                   'pth': _quoted(_PTH_FILENAME),
                   'pth_raw': _PTH_FILENAME,
           })
    _run(host,
         cmd,
         timeout=_QUICK_CMD_TIMEOUT_SECONDS,
         description='Writing %s on the DUT' % _PTH_FILENAME)


def _remove_scratch(host):
    """Removes every scratch path this module creates.

    Best-effort, including in the pre-flight: scratch that fails to delete is
    then counted as used space by the free space check that follows, which errs
    on the strict side and is the right way round.

    Note that this only runs when a deploy does. If autoserv is killed outright
    (lab timeout, drone restart) the caller's cleanup never runs and the
    scratch directory survives; nothing else in the tree knows that path, so it
    persists until the next deploy reuses it or the DUT is reimaged. Both
    happen often enough that a dedicated repair action is not worth the surface
    area.

    Args:
        host: Autotest host object for the DUT.
    """
    _run_best_effort(host,
                     'rm -rf %s' % _quoted(*_SCRATCH_PATHS),
                     timeout=_EXTRACT_TIMEOUT_SECONDS,
                     description='Removing Telemetry scratch directories')


def _handle_kill_switch(host, force):
    """Acts on the kill switch, if it is off.

    @param host: Autotest host object for the DUT.
    @param force: Whether the caller asked for an unconditional deploy.

    @returns True if deployment is disabled and the DUT already has Telemetry,
             so there is nothing to do and nothing to complain about. False if
             the switch is on and the deploy should proceed.

    @raises TelemetryDeployError: If deployment is disabled and the DUT does not
                                  have Telemetry.
    """
    if _deploy_enabled():
        return False

    # Only worth a round trip on this path: with the switch off, whether the
    # DUT already has Telemetry is the difference between "nothing to do" and
    # "this test cannot possibly work".
    if not force and is_telemetry_on_dut(host):
        logging.debug(
                'On-demand Telemetry deployment is disabled via [CROS] %s, but '
                '%s already has Telemetry.', _ENABLE_CONFIG_KEY, host.hostname)
        return True

    raise TelemetryDeployError(
            'Telemetry is not installed on %s and on-demand deployment is '
            'disabled via [CROS] %s, so it cannot be installed. Re-enable that '
            'setting, or run this test on an image that ships Telemetry.' %
            (host.hostname, _ENABLE_CONFIG_KEY))


def ensure_telemetry_on_dut(host,
                            build=None,
                            cache_endpoint=None,
                            bucket=None,
                            force=False):
    """Ensures the Telemetry framework is available at /usr/local/telemetry.

    On images that still ship Telemetry this returns immediately without
    modifying the DUT, which is what makes it safe to call unconditionally
    today.

    Ordering note: any pre-existing tree is deleted before the new one is
    extracted, because the partition does not have room for both. That deletion
    is deferred until the payload is already on the DUT, so everything that can
    fail without one -- an unresolvable build, a 404, a network failure, a full
    partition -- leaves the DUT exactly as it was. telemetry.pth is written
    before _swap_into_place() so the rename into TELEMETRY_DUT_DIR remains the
    single atomic publish step; any failure prior to that rename leaves no live
    tree behind and says so in the log if the prior tree was removed.

    Args:
        host: Autotest host object for the DUT.
        build: Build to fetch the dep for. Defaults to the build recorded in the
            host info store.
        cache_endpoint: Cache server endpoint (host:port). In CFT this is
            resolved outside of tauto and should be passed in.
        bucket: GS bucket to fetch the dep from. Defaults to the same bucket
            telemetry_setup uses for the drone-side tree.
        force: Deploy even if Telemetry is already present. Used to canary the
            deploy path while images still ship Telemetry, and to replace an
            image's Telemetry when a newer Chrome has been side-loaded onto the
            DUT.

    Returns:
        True if this call deployed Telemetry, and the caller is therefore
        responsible for removing it again. False means Telemetry was already
        present and came from somewhere else.

    Raises:
        TelemetryDeployError: If Telemetry is needed but cannot be made
            available, including when deployment is disabled by config.
    """
    if _handle_kill_switch(host, force):
        return False

    if not force and is_telemetry_on_dut(host):
        logging.debug('Telemetry is already present on %s; nothing to deploy.',
                      host.hostname)
        return False

    build = _resolve_build(host, build)
    bucket = bucket or telemetry_setup.DEFAULT_DEPS_BUCKET
    logging.info('Deploying Telemetry to %s for build %s.', host.hostname,
                 build)

    _remove_scratch(host)
    _check_free_space(host)

    start = time.monotonic()
    removing_existing_tree = False
    try:
        tarball_path, tarball = _download_payload(host, build, cache_endpoint,
                                                  bucket)
        downloaded = time.monotonic()

        removing_existing_tree = True
        _remove_existing_tree(host)

        # Write telemetry.pth before swapping the staged tree into place so
        # _swap_into_place() remains the single atomic commit step; site.py
        # ignores .pth entries until the target directory exists on disk.
        _extract_on_dut(host, tarball_path, tarball)
        _ensure_python_path(host)
        _swap_into_place(host)
    except Exception:
        if removing_existing_tree:
            logging.error(
                    'Telemetry deployment failed at or after the removal of '
                    'the existing tree, so %s may no longer have a usable '
                    'Telemetry tree at %s.', host.hostname, TELEMETRY_DUT_DIR)
        raise
    finally:
        # On success this only removes the tarball, since the staged tree has
        # been renamed away. On failure it is what stops a partial tree from
        # being stranded on the partition this change exists to free up --
        # nothing else would ever remove it, because the caller is never told
        # that a deployment happened.
        _remove_scratch(host)
    done = time.monotonic()

    logging.info(
            'Deployed Telemetry to %s from %s in %.1fs (download %.1fs, '
            'extract %.1fs).', host.hostname, tarball, done - start,
            downloaded - start, done - downloaded)
    return True


def cleanup_telemetry_on_dut(host):
    """Removes the deployed Telemetry tree and any leftover scratch directories.

    Only call this for a tree that this module deployed. Deleting Telemetry that
    came from the image would surprise the next test to run on the DUT, at least
    until images stop shipping it.

    Note that telemetry.pth is deliberately left in place: on ChromeOS test
    images, rootfs /usr/lib/python3.* is a symlink to
    /usr/local/usr/lib/python3.* and /usr/local/usr is a symlink to '.', so
    /usr/lib/python3.*/site-packages/telemetry.pth and
    /usr/local/lib/python3.*/site-packages/telemetry.pth are the same file on
    stateful. Removing it here would delete the .pth file shipped by the image,
    whereas leaving it in place is a no-op once TELEMETRY_DUT_DIR is gone
    (site.py ignores .pth entries whose target directory does not exist, and
    is_telemetry_on_dut() checks _SENTINEL_PATHS first).

    This is best-effort: it runs at the end of a test, frequently while an
    exception is already propagating, and sometimes against a DUT that has gone
    away. Failing here would replace a real test failure with a cleanup error.

    Args:
        host: Autotest host object for the DUT.
    """
    logging.info('Removing the deployed Telemetry tree from %s.',
                 host.hostname)
    _run_best_effort(host,
                     'rm -rf %s' % _quoted(TELEMETRY_DUT_DIR, *_SCRATCH_PATHS),
                     timeout=_EXTRACT_TIMEOUT_SECONDS,
                     description='Removing the deployed Telemetry tree')

# -*- coding: utf-8 -*-
# Copyright 2021 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A class that sets up the environment for telemetry testing."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from autotest_lib.client.common_lib import packages
from autotest_lib.client.common_lib import utils
from autotest_lib.client.common_lib.cros import dev_server

import contextlib
import errno
import fcntl
import logging
import os
import re
import shutil
import subprocess
import tempfile

import requests

_READ_BUFFER_SIZE_BYTES = 1024 * 1024  # 1 MB

# curl --fail reports every HTTP status >= 400 as packages.CURL_HTTP_ERROR_EXIT,
# so the status code parsed from -sS stderr is passed to
# packages.is_http_not_found() to tell a 404/410 apart from, say, a 503.
# Older curl versions append the reason phrase ("404 Not Found").
_CURL_HTTP_STATUS_RE = re.compile(r'returned error: (\d{3})\b')

# Timeout for the HTTP request and for the SSH fallback's connection setup.
# The SSH curl transfer limit is generous because the dependency tarballs are
# large.
_REQUEST_TIMEOUT_SECONDS = 60
_SSH_CURL_MAX_TIME_SECONDS = 30 * 60

DEFAULT_DEPS_BUCKET = "chromeos-image-archive"
STAGING_DEPS_BUCKET = "staging-chromeos-image-archive"

# Partial devserver URL for pulling a single dependency tarball out of a build's
# autotest_packages.tar. Formatted with
# (devserver_url, gs_bucket, build, tarball_name).
#
# Shared with server/cros/telemetry_deploy.py, which uses the same endpoint to
# fetch the Telemetry dep directly onto a DUT.
STATIC_URL_TEMPLATE = (
        '%s/extract/%s/%s/autotest_packages.tar?file=autotest/packages/%s')


@contextlib.contextmanager
def lock_dir(dir_name):
    """Lock a directory exclusively by placing a file lock in it.

    Args:
      dir_name: the directory name to be locked.
    """
    lock_file = os.path.join(dir_name, '.lock')
    with open(lock_file, 'w+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class TelemetrySetupError(Exception):
    """Exception class used by this module."""
    pass


class TelemetryExtractError(TelemetrySetupError):
    """tar ran but failed to extract the archive."""
    pass


class TelemetrySetup(object):
    """Class that sets up the environment for telemetry testing."""

    # Relevant directory paths.
    _BASE_DIR_PATH = '/tmp/telemetry-workdir'
    _PARTIAL_DEPENDENCY_DIR_PATH = 'autotest/packages'

    # Relevant directory names.
    _TELEMETRY_SRC_DIR_NAME = 'telemetry_src'
    _TEST_SRC_DIR_NAME = 'test_src'
    _SRC_DIR_NAME = 'src'

    # Names of the telemetry dependency packages. Each is published as a
    # dep-<name> tarball under one of the names that
    # packages.PackageManager.get_tarball_names() lists.
    _DEPENDENCIES = (
            'telemetry_dep',
            'chrome_test',
            'perf_data_dep',
    )

    # Partial devserver URLs.
    _STATIC_URL_TEMPLATE = STATIC_URL_TEMPLATE

    def __init__(self, hostname, build, override_gs_bucket=None):
        """Initializes the TelemetrySetup class.

        Args:
        hostname: The host for which telemetry environment should be setup. This
            is important for devserver resolution.
        build: The build for which telemetry environment should be setup. It is
            typically in the format <board>/<version>.
        override_gs_bucket: The name of the bucket to grab artifacts from. If
            None is passed, DEFAULT_DEPS_BUCKET will be used.
        """
        self._build = build
        self._gs_bucket = (DEFAULT_DEPS_BUCKET if override_gs_bucket is None
                           else override_gs_bucket)
        self._ds = dev_server.ImageServer.resolve(self._build,
                                                  hostname=hostname)
        self._setup_dir_path = tempfile.mkdtemp(prefix='telemetry-setupdir_')
        self._tmp_build_dir = os.path.join(self._BASE_DIR_PATH, self._build)
        self._tlm_src_dir_path = os.path.join(self._tmp_build_dir,
                                              self._TELEMETRY_SRC_DIR_NAME)
        self._skipped_suffixes = set()

    def Setup(self):
        """Sets up the environment for telemetry testing.

        This method downloads the telemetry dependency tarballs and extracts
        them into a 'src' directory.

        Returns:
          Path to the src directory where the telemetry dependencies have been
              downloaded and extracted.

        Raises:
          TelemetrySetupError: If the telemetry dependencies cannot be
              downloaded, extracted, or moved into place.
        """
        src_folder = os.path.join(self._tlm_src_dir_path, self._SRC_DIR_NAME)
        test_src = os.path.join(self._tlm_src_dir_path,
                                self._TEST_SRC_DIR_NAME)
        self._MkDirP(self._tlm_src_dir_path)
        with lock_dir(self._tlm_src_dir_path):
            if not os.path.exists(src_folder):
                # Download the required dependency tarballs.
                for dep in self._DEPENDENCIES:
                    if (not self._FetchAndExtract(dep)
                                and dep == 'telemetry_dep'):
                        note = (' (%s was skipped because zstd is not '
                                'installed)' % packages.ZSTD_TARBALL_SUFFIX
                                if not packages.has_zstd() else '')
                        raise TelemetrySetupError(
                                'dep-telemetry_dep was not found for build '
                                '%s%s.' % (self._build, note))

                if not os.path.isdir(test_src):
                    raise TelemetrySetupError(
                            'No %s/ directory after extracting dependencies '
                            'for build %s.' %
                            (self._TEST_SRC_DIR_NAME, self._build))

                # By default all the tarballs extract to test_src but some parts
                # of the telemetry code specifically hardcoded to exist inside
                # of 'src'.
                try:
                    shutil.move(test_src, src_folder)
                except OSError as e:
                    raise TelemetrySetupError(
                            'Failure in telemetry setup for build %s. Appears '
                            'that the test_src to src move failed: %s' %
                            (self._build, e))
        return src_folder

    def _FetchAndExtract(self, dep):
        """Downloads and extracts a dependency, falling back on zstd failure.

        Args:
          dep: Name of the dependency package, e.g. 'telemetry_dep'.

        Returns:
            True if the dependency was downloaded and extracted, or False if no
            usable format of the dependency is published.
        """
        dep_path = self._DownloadDependency(dep,
                                            exclude_suffixes=tuple(
                                                    self._skipped_suffixes))
        if not dep_path:
            return False
        try:
            self._ExtractTarball(dep_path, self._tlm_src_dir_path)
        except TelemetryExtractError as e:
            # TODO(b/580903568): Drop the .tar.bz2 fallback once pre-R158
            # branches (including R156 LTS) reach EOL.
            if not dep_path.endswith(packages.ZSTD_TARBALL_SUFFIX):
                raise
            logging.warning('%s; falling back to %s.', e,
                            packages.BZIP2_TARBALL_SUFFIX)
            self._RemoveQuietly(dep_path)
            self._skipped_suffixes.add(packages.ZSTD_TARBALL_SUFFIX)
            fallback = self._DownloadDependency(
                    dep, exclude_suffixes=tuple(self._skipped_suffixes))
            if not fallback:
                raise
            self._ExtractTarball(fallback, self._tlm_src_dir_path)
        return True

    def _DownloadDependency(self, dep_name, exclude_suffixes=()):
        """Downloads a dependency tarball in whichever supported format exists.

        Candidates come from packages.PackageManager.get_tarball_names(), which
        lists packages.DEFAULT_TARBALL_SUFFIX first, so a build that publishes
        the default format costs a single request. Older builds may only
        publish bzip2, and transitional builds may publish both formats.

        Args:
          dep_name: Name of the dependency package, e.g. 'telemetry_dep'.
          exclude_suffixes: Compression suffixes to skip, e.g. after a failed
              extraction.

        Returns:
            Full path to the downloaded tarball, or None if this build does not
            publish the dependency in any supported format.
        """
        # TODO(b/580903568): Simplify multi-format candidate probing once
        # pre-R158 branches (including R156 LTS) reach EOL.
        all_candidates = packages.PackageManager.get_tarball_names(
                dep_name, 'dep')
        zstd_missing = not packages.has_zstd()
        if (zstd_missing and packages.ZSTD_TARBALL_SUFFIX
                    not in self._skipped_suffixes):
            logging.warning(
                    'zstd is not installed on this host; skipping %s '
                    'telemetry dependency tarballs.',
                    packages.ZSTD_TARBALL_SUFFIX)
            self._skipped_suffixes.add(packages.ZSTD_TARBALL_SUFFIX)
        skipped = set(exclude_suffixes) | self._skipped_suffixes
        if skipped:
            candidates = [
                    c for c in all_candidates if not c.endswith(tuple(skipped))
            ]
        else:
            candidates = all_candidates
        for filename in candidates:
            dep_path = self._DownloadFilesFromDevserver(
                    filename, self._setup_dir_path)
            if dep_path:
                return dep_path

        # Not fatal here: a dependency can be absent because it is new, because
        # it was dropped, or because this build predates it.
        note = (' (%s skipped because zstd is not installed)' %
                packages.ZSTD_TARBALL_SUFFIX if zstd_missing else '')
        logging.warning('None of %s were found for build %s%s.',
                        ', '.join(candidates), self._build, note)
        return None

    def _DownloadFilesFromDevserver(self, filename, dest_path):
        """Downloads the given tarball from the devserver.

        Args:
          filename: Name of the tarball to be downloaded.
          dest_path: Full path to the directory where it should be downloaded.

        Returns:
            Full path to the downloaded file, or None if the devserver does not
            have it (HTTP 404/410).

        Raises:
          TelemetrySetupError when the download cannot be completed for any
              other reason.
        """
        dep_path = os.path.join(dest_path, filename)
        url = (self._STATIC_URL_TEMPLATE %
               (self._ds.url(), self._gs_bucket, self._build, filename))
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            with open(dep_path, 'wb') as f:
                for content in resp.iter_content(_READ_BUFFER_SIZE_BYTES):
                    f.write(content)
        except Exception as e:
            if (
                    isinstance(e, requests.exceptions.HTTPError)
                    and packages.is_http_not_found(
                            packages.CURL_HTTP_ERROR_EXIT, resp.status_code)):
                # Routine: each dependency is probed once per compression
                # format, so the format this build does not use is absent.
                logging.debug('The request %s returned HTTP %s (not found).',
                              url, resp.status_code)
                return None
            elif isinstance(e, requests.exceptions.ConnectionError):
                logging.warning(
                        'The request failed because a connection to the devserver '
                        '%s could not be established. Attempting to execute the '
                        'request %s once by SSH-ing into the devserver.',
                        self._ds.url(), url)
                return self._DownloadFilesFromDevserverViaSSH(url, dep_path)
            elif isinstance(e, requests.exceptions.Timeout):
                logging.warning(
                        'Request %s timed out after %ds; retrying once over '
                        'SSH via %s.', url, _REQUEST_TIMEOUT_SECONDS,
                        self._ds.url())
                return self._DownloadFilesFromDevserverViaSSH(url, dep_path)
            else:
                raise TelemetrySetupError(
                        'An error occurred while trying to complete  %s: %s' %
                        (url, e))
        logging.info('Downloaded telemetry dependency %s from %s.', filename,
                     url)
        return dep_path

    def _DownloadFilesFromDevserverViaSSH(self, url, dep_path):
        """Downloads the file at the URL from the devserver by SSH-ing into it.

        Args:
          url: URL of the location of the tarball on the devserver.
          dep_path: Full path to the file where it will be downloaded.

        Returns:
            Full path to the downloaded file, or None if the devserver does not
            have the file (HTTP 404/410).

        Raises:
          TelemetrySetupError when the download cannot be completed for any
              other reason, including any other HTTP error.
        """
        # --fail makes curl exit non-zero on an HTTP error instead of writing
        # the error page into a file that is about to be handed to tar, and -sS
        # keeps the stderr line from which _CURL_HTTP_STATUS_RE parses the HTTP
        # status code (without relying on curl >= 7.63's %{stderr}). ssh hands
        # the command to the remote shell, so the URL, which embeds the build
        # label and a '?', is quoted.
        remote_cmd = 'curl --fail -sS --max-time %d %s' % (
                _SSH_CURL_MAX_TIME_SECONDS, utils.sh_quote_word(url))
        cmd = [
                'ssh',
                '-n',
                '-o',
                'BatchMode=yes',
                '-o',
                'ConnectTimeout=%d' % _REQUEST_TIMEOUT_SECONDS,
                '-o',
                'ServerAliveInterval=30',
                '-o',
                'ServerAliveCountMax=3',
                self._ds.hostname,
                remote_cmd,
        ]
        with open(dep_path, 'wb') as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.PIPE)
            _, err = proc.communicate()
        err = err.decode('utf-8', 'replace')

        match = _CURL_HTTP_STATUS_RE.search(err)
        http_code = match.group(1) if match else ''
        if packages.is_http_not_found(proc.returncode, http_code):
            # Same meaning as 404/410 over the regular path: this build does
            # not publish the file.
            logging.debug('The request %s returned HTTP %s (not found).', url,
                          http_code)
            self._RemoveQuietly(dep_path)
            return None

        if proc.returncode != 0:
            self._RemoveQuietly(dep_path)
            raise TelemetrySetupError(
                    'The command: %s finished with returncode %s and '
                    'errors as following: %s. The telemetry dependency '
                    'could not be downloaded.' %
                    (' '.join(cmd), proc.returncode, err.strip()))
        logging.info('Downloaded telemetry dependency %s from %s via SSH.',
                     os.path.basename(dep_path), url)
        return dep_path

    def _RemoveQuietly(self, path):
        """Deletes a file, ignoring failures.

        Args:
          path: Full path to the file to delete.
        """
        try:
            os.remove(path)
        except OSError as e:
            logging.debug('Could not remove %s: %s', path, e)

    def _ExtractTarball(self, tarball_path, dest_path):
        """Extracts the given tarball into the destination directory.

        The decompressor is chosen from the file name rather than left to tar's
        own detection, which only recognizes zstd on GNU tar 1.31 or newer.

        Args:
          tarball_path: Full path to the tarball to be extracted.
          dest_path: Full path to the directory where the tarball should be
              extracted.

        Raises:
          TelemetrySetupError if the method is unable to extract the tarball for
              any reason.
        """
        if tarball_path.endswith(packages.ZSTD_TARBALL_SUFFIX):
            decompress = ['-I', 'zstd']
        # TODO(b/580903568): Drop .tar.bz2 support once pre-R158 branches
        # (including R156 LTS) reach EOL.
        elif tarball_path.endswith(packages.BZIP2_TARBALL_SUFFIX):
            decompress = ['-j']
        else:
            raise TelemetrySetupError('Unsupported tarball format: %s' %
                                      tarball_path)
        cmd = (['tar', '--no-same-owner', '-x'] + decompress +
               ['-f', tarball_path, '--directory', dest_path])
        logging.info('Extracting telemetry dependency: %s', ' '.join(cmd))
        try:
            proc = subprocess.Popen(cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            _, err = proc.communicate()
        except Exception as e:
            shutil.rmtree(dest_path)
            raise TelemetrySetupError(
                    'An exception occurred while trying to untar %s into %s: %s'
                    % (tarball_path, dest_path, str(e)))
        if proc.returncode != 0:
            raise TelemetryExtractError(
                    'tar exited with %d while extracting %s into %s: %s' %
                    (proc.returncode, tarball_path, dest_path,
                     err.decode('utf-8', 'replace').strip()))

    def _MkDirP(self, path):
        """Recursively creates the given directory.

        Args:
          path: Full path to the directory that needs to the created.

        Raises:
          TelemetrySetupError is the method is unable to create directories for
              any reason except OSError EEXIST which indicates that the
              directory already exists.
        """
        try:
            os.makedirs(path)
        except Exception as e:
            if not isinstance(e, OSError) or e.errno != errno.EEXIST:
                raise TelemetrySetupError(
                        'Could not create directory %s due to %s.' %
                        (path, str(e)))

    def Cleanup(self):
        """Cleans up telemetry setup and work environment."""
        try:
            shutil.rmtree(self._setup_dir_path)
        except Exception as e:
            logging.error('Something went wrong. Could not delete %s: %s',
                          self._setup_dir_path, e)
        try:
            shutil.rmtree(self._tlm_src_dir_path)
        except Exception as e:
            logging.error('Something went wrong. Could not delete %s: %s',
                          self._tlm_src_dir_path, e)

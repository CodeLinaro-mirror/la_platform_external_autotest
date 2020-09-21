# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import logging
import os

from autotest_lib.client.common_lib import error
from autotest_lib.client.common_lib.cros import kernel_utils
from autotest_lib.client.cros import constants
from autotest_lib.server.cros.update_engine import update_engine_test


class autoupdate_EndToEndTest(update_engine_test.UpdateEngineTest):
    """Complete update test between two Chrome OS releases.

    Performs an end-to-end test of updating a ChromeOS device from one version
    to another. The test performs the following steps:

      - Stages the source (full) and target update payloads on a devserver.
      - Installs source image on the DUT (if provided) and reboots to it.
      - Verifies that sign in works correctly on the source image.
      - Installs target image on the DUT and reboots.
      - Does a final update check.
      - Verifies that sign in works correctly on the target image.
      - Returns the hostlogs collected during each update check for
        verification against expected update events.

    This class interacts with several others:
    UpdateEngineTest: base class for comparing expected update events against
                      the events listed in the hostlog.
    UpdateEngineEvent: class representing a single expected update engine event.
    ChromiumOSTestPlatform: A class representing the Chrome OS device we are
                            updating. It has functions for things the DUT can
                            do: get logs, reboot, start update etc

    """
    version = 1

    _LOGIN_TEST = 'login_LoginSuccess'


    def cleanup(self):
        """Save the logs from stateful_partition's preserved/log dir."""
        stateful_preserved_logs = os.path.join(self.resultsdir,
                                               'stateful_preserved_logs')
        os.makedirs(stateful_preserved_logs)
        self._host.get_file(constants.AUTOUPDATE_PRESERVE_LOG,
                            stateful_preserved_logs, safe_symlinks=True,
                            preserve_perm=False)
        super(autoupdate_EndToEndTest, self).cleanup()


    def _get_hostlog_file(self, filename, identifier):
        """Return the hostlog file location.

        This hostlog file contains the update engine events that were fired
        during the update.

        @param filename: The partial filename to look for.
        @param identifier: A string that is appended to the logfile when it is
                           saved so that multiple files with the same name can
                           be differentiated.

        """
        hostlog = '%s_%s_%s' % (filename, self._host.hostname, identifier)
        file_url = os.path.join(self.resultsdir,
                                hostlog)
        if os.path.exists(file_url):
            logging.info('Hostlog file to be used for checking update '
                         'steps: %s', file_url)
            return file_url
        raise error.TestFail('Could not find %s' % filename)


    def run_update_test(self, test_conf):
        """Runs the update test and checks it succeeded.

        @param test_conf: A dictionary containing test configuration values.

        """
        # Record the active root partition.
        active, inactive = kernel_utils.get_kernel_state(self._host)
        logging.info('Source active slot: %s', active)

        source_release = test_conf['source_release']
        target_release = test_conf['target_release']

        self.update_device(test_conf['target_payload_uri'], tag='target')

        # Compare hostlog events from the update to the expected ones.
        rootfs = self._get_hostlog_file(self._DEVSERVER_HOSTLOG_ROOTFS,
                                        'target')
        reboot = self._get_hostlog_file(self._DEVSERVER_HOSTLOG_REBOOT,
                                        'target')

        self.verify_update_events(source_release, rootfs)
        self.verify_update_events(source_release, reboot, target_release)
        kernel_utils.verify_boot_expectations(inactive, host=self._host)
        logging.info('Update successful, test completed')


    def run_once(self, test_conf):
        """Performs a complete auto update test.

        @param test_conf: a dictionary containing test configuration values.

        """
        logging.debug('The test configuration supplied: %s', test_conf)
        self._autotest_devserver = self._get_devserver_for_test(test_conf)
        self._stage_payloads(test_conf['source_payload_uri'],
                             test_conf['source_archive_uri'])

        self._stage_payloads(test_conf['target_payload_uri'],
                             test_conf['target_archive_uri'])

        # Install source image
        source_payload_uri = test_conf['source_payload_uri']
        if source_payload_uri is not None:
            self.update_device(source_payload_uri, clobber_stateful=True)
            self._run_client_test_and_check_result(self._LOGIN_TEST,
                                                   tag='source')
        # Start the update to the target image.
        self.run_update_test(test_conf)

        # Check we can login after the update.
        self._run_client_test_and_check_result(self._LOGIN_TEST, tag='target')

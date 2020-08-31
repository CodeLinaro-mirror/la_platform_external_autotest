# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


"""The autotest performing FW update, both EC and AP in CCD mode."""
import logging

from autotest_lib.client.common_lib import error
from autotest_lib.server.cros.faft.cr50_test import Cr50Test


class firmware_Cr50CCDFirmwareUpdate(Cr50Test):
    """A test that can provision a machine to the correct firmware version."""

    version = 1
    should_restore_fw = False

    def initialize(self, host, cmdline_args, full_args):
        """Initialize the test and check if cr50 exists.

        Raises:
            TestNAError: If the dut is not proper for this test for its RDD
                         recognition problem.
        """
        super(firmware_Cr50CCDFirmwareUpdate,
              self).initialize(host, cmdline_args, full_args)

        # Don't bother if there is no Chrome EC.
        if not self.check_ec_capability():
            raise error.TestNAError('Nothing needs to be tested on this device')

        servo_type = self.servo.get_servo_version()
        if 'ccd_cr50' not in servo_type:
            raise error.TestNAError('unsupported servo type: %s' % servo_type)

        if eval(full_args.get('backup_fw', 'False')):
            self.backup_firmware()

    def cleanup(self):
        try:
            if not self.should_restore_fw:
                return

            self.cr50.reboot()
            self.switcher.mode_aware_reboot(reboot_type='cold')

            if self.is_firmware_saved():
                logging.info('Restoring firmware')
                self.restore_firmware()
            else:
                logging.info('chromeos-firmwareupdate --mode=recovery')
                result = self._client.run('chromeos-firmwareupdate'
                                          ' --mode=recovery',
                                          ignore_status=True)
                if result.exit_status != 0:
                    logging.error('chromeos-firmwareupdate failed: %s',
                                  result.stdout.strip())
                self._client.reboot()
        except Exception as e:
            logging.error("Caught exception: %s", str(e))
        finally:
            super(firmware_Cr50CCDFirmwareUpdate, self).cleanup()

    def run_once(self, host, rw_only=False):
        """The method called by the control file to start the test.

        Args:
          host:  a CrosHost object of the machine to update.
          rw_only: True to only update the RW firmware.

        Raises:
          TestFail: if the firmware version remains unchanged.
          TestError: if the latest firmware release cannot be located.
          TestNAError: if the test environment is not properly set.
                       e.g. the servo type doesn't support this test.
        """

        # Get the parent (a.k.a. referebce board or baseboard), and hand it
        # to get_latest_release_version so that it
        # can use it in search as secondary candidate. For example, bob doesn't
        # have its own release directory, but its parent, gru does.
        parent = getattr(self.faft_config, 'parent', None)

        value = host.get_latest_release_version(self.faft_config.platform,
                                                parent)
        if not value:
            raise error.TestError('Cannot locate the latest release for %s' %
                                  self.faft_config.platform)

        # Fast open cr50 and check if testlab is enabled.
        self.fast_ccd_open(enable_testlab=True)
        if self.servo.has_control('active_v4_device'):
            try:
                self.servo.set('active_v4_device', 'ccd_cr50')
            except error.TestFail as e:
                raise error.TestNAError('cannot change active_v4_device: %s' %
                                        str(e))

        # If it is ITE EC, then ccd reset factory.
        if self.servo.get('ec_chip') == 'it83xx':
            self.cr50.set_cap('I2C', 'Always')

        self.should_restore_fw = True
        host.firmware_install(build=value, rw_only=rw_only,
                              dest=self.resultsdir, verify_version=True)

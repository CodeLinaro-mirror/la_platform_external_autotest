# Copyright 2018 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import logging

from autotest_lib.server.cros.faft.fingerprint_test import FingerprintTest


class firmware_Fingerprint(FingerprintTest):
    """
    Common class for running fingerprint firmware tests. Initializes the
    firmware to a known state and then runs the test executable with
    specified arguments on the DUT.
    """
    version = 1

    def run_once(self, test_exe, test_exe_args=None,
                 use_dev_signed_fw=False,
                 enable_hardware_write_protect=True,
                 enable_software_write_protect=True,
                 force_firmware_flashing=False,
                 init_entropy=True):
        """Run the test."""
        test_dir = os.path.join(self.bindir, 'tests/')
        logging.info('test_dir: %s', test_dir)

        # Initialize DUT state and set up tmp working directory on device.
        self.setup_test(
            test_dir, use_dev_signed_fw, enable_hardware_write_protect,
            enable_software_write_protect, force_firmware_flashing,
            init_entropy)

        self._test_exe = test_exe

        # Convert the arguments (test image names) to the actual filenames of
        # the test images.
        image_args = []
        if test_exe_args:
            for arg in test_exe_args:
                image_args.append(getattr(self, arg))
        self._test_exe_args = image_args

        logging.info('Running test: %s', self._test_exe)
        self.run_test(self._test_exe, *self._test_exe_args)

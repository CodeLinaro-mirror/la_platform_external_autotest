# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from autotest_lib.server.cros.faft.fingerprint_test import FingerprintTest


class firmware_FingerprintSigner(FingerprintTest):
    """
    This test does minimal initialization, only checking the firmware file.
    """
    version = 1

    def run_once(self):
        """Run the test: verify that the key is MP, not Pre-MP"""
        self.validate_build_fw_file(allowed_types=[self._KEY_TYPE_MP])

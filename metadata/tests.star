# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

TESTS = []
def _append_tests(tests):
    for t in tests:
        TESTS.append(t)

load("//client/site_tests/dummy_Pass/wifichaos.star", client_dummy_Pass_wifichaos = "TESTS")
_append_tests(client_dummy_Pass_wifichaos)

load("//client/site_tests/dummy_Pass/def.star", client_dummy_Pass_def = "TESTS")
_append_tests(client_dummy_Pass_def)

load("//client/site_tests/dummy_Fail/def.star", client_dummy_Fail_def = "TESTS")
_append_tests(client_dummy_Fail_def)

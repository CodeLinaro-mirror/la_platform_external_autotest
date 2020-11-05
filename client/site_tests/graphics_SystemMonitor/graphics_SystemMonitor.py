# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Client-side test used to monitor system metrics associated with graphics rendering performance"""

import logging

import time

from autotest_lib.client.bin import test
from autotest_lib.client.cros.power import power_test

#  class graphics_SystemMonitor(power_test.power_Test):
class graphics_SystemMonitor(power_test.power_Test):
    """Wrapper around power_Test for acquiring system metrics related to graphics rendering
    performance (temperature, clock freqs, power states)

    This should only be called from a compatible "server" test such as graphcs_TraceReplayExtended,
    thus no control file has been defined
    """

    version = 1

    def initialize(self, sample_rate_seconds=1, pdash_note=''):
        super(graphics_SystemMonitor, self).initialize(
            seconds_period=sample_rate_seconds,
            pdash_note=pdash_note,
            force_discharge=False)

    def run_once(self, max_duration_minutes=1.0):
        """Setup system loggers and wait until max_duration_minutes elapses or stop signal is sent by server

        temporal data logs are written to <test_results>/{power,cpu,temp,fan_rpm}_results_<timestamp>_raw.txt
        """
        #TODO Do we want to enable perfmode? autotest_lib.client.cros.perf.PerfControl()
        self.start_measurements() # in power_Test()
        time_start = time.time()
        time_now = time_start
        time_end = time_now + max_duration_minutes*60.0
        while (time_now < time_end):
            time.sleep(1)
            time_now = time.time()

        #TODO if we have server->client communication we can create a checkpoint for each trace replay
        #     otherwise, we need to manually segment the temporal logs based on timestamps in
        #     tast.TraceReplayExtended logs
        self.checkpoint_measurements('all')

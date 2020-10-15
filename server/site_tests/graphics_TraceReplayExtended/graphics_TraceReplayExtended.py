# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import logging
import os
import threading
import time

from autotest_lib.client.common_lib import error
from autotest_lib.server import autotest
from autotest_lib.server import test
from autotest_lib.server.site_tests.tast import tast

class SystemMonitorThread(threading.Thread):
    def __init__(self, host, max_duration_minutes):
        super(SystemMonitorThread, self).__init__(name=__name__)
        self.autotest_client = autotest.Autotest(host)
        self.max_duration_minutes = max_duration_minutes

    def run(self):
        logging.info("started sysmon thread")
        self.autotest_client.run_test('graphics_SystemMonitor',
                                      max_duration_minutes=self.max_duration_minutes,
                                      sample_rate_seconds=1)

class TastManagerThread(threading.Thread):
    def __init__(self, host, tast_instance, client_tast_test, tast_command_args=[]):
        super(TastManagerThread, self).__init__(name=__name__)
        self.tast = tast_instance
        self.tast.initialize(host=host,
                             test_exprs=[client_tast_test],
                             ignore_test_failures=True,
                             max_run_sec=10800,
                             command_args=tast_command_args)

    def run(self):
        logging.info("started tast thread")
        self.tast.run_once()

class graphics_TraceReplayExtended(test.test):
    version = 1

    def run_once(self, host, client_tast_test, max_duration_minutes=35, tast_command_args=[]):
        # attach to test device (autotest-client)
        logging.debug('connecting to autotest client on host')
        system_monitor = SystemMonitorThread(host, max_duration_minutes)
        system_monitor.start()
        logging.info("Waiting 10s for system monitor to initialize...")
        time.sleep(10) # give time for initialization

        ###### TAST TEST ######
        logging.info("Running Tast test: \"%s\".", client_tast_test)
        tast_outputdir = os.path.join(self.outputdir, 'tast')
        if not os.path.exists(tast_outputdir):
            logging.debug('making tast outputdir: "%s"', tast_outputdir)
            os.makedirs(tast_outputdir)

        tast_instance = tast.tast(job=self.job, bindir=self.bindir, outputdir=tast_outputdir)
        tast_manager = TastManagerThread(host, tast_instance, client_tast_test, tast_command_args)
        tast_manager.start()

        # TODO need to up the tast class's timout_sec param for long runs
        ###### END TAST TEST ######

        threads = [system_monitor, tast_manager]
        while any((thread.is_alive() for thread in threads)):
            for thread in list(threads):
                if not thread.is_alive():
                    logging.info('Thread "%s" has ended', thread.__class__.__name__)
                    threads.remove(thread)
            time.sleep(1)

        #TODO Post-processing
        #  logging.info("Beginning data post-processing. Test data is in %s", self.outputdir)

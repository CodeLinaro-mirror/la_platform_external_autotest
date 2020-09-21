# Copyright 2018 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import logging
import re
import time

from autotest_lib.client.bin import test
from autotest_lib.client.common_lib import error
from autotest_lib.client.cros import ec
from autotest_lib.client.cros import service_stopper
from autotest_lib.client.cros.power import power_dashboard
from autotest_lib.client.cros.power import power_status
from autotest_lib.client.cros.power import power_telemetry_utils
from autotest_lib.client.cros.power import power_utils
from autotest_lib.client.cros.video import histogram_verifier

class power_Test(test.test):
    """Optional base class power related tests."""
    version = 1

    keypress_histogram = 'Event.Latency.EndToEnd.KeyPress'
    histogram_re = 'Histogram: %s recorded (\d+) samples, mean = (\d+\.\d+)'
    hist_percentile_re = '^(\d+).+\{(\d+)\.\d+\%\}'

    def initialize(self, seconds_period=20., pdash_note='',
                   force_discharge=False):
        """Perform necessary initialization prior to power test run.

        @param seconds_period: float of probing interval in seconds.
        @param pdash_note: note of the current run to send to power dashboard.
        @param force_discharge: force battery to discharge during the test.

        @var backlight: power_utils.Backlight object.
        @var keyvals: dictionary of result keyvals.
        @var status: power_status.SysStat object.

        @var _checkpoint_logger: power_status.CheckpointLogger to track
                                 checkpoint data.
        @var _psr: power_utils.DisplayPanelSelfRefresh object to monitor PSR.
        @var _services: service_stopper.ServiceStopper object.
        @var _start_time: float of time in seconds since Epoch test started.
        @var _stats: power_status.StatoMatic object.
        @var _meas_logs: list of power_status.MeasurementLoggers
        """
        super(power_Test, self).initialize()
        self.backlight = power_utils.Backlight()
        self.backlight.set_default()
        self.keyvals = dict()
        self.status = power_status.get_status()

        self._checkpoint_logger = power_status.CheckpointLogger()
        self._seconds_period = seconds_period

        self._force_discharge = force_discharge
        if force_discharge:
            if not self.status.battery:
                raise error.TestNAError('DUT does not have battery. '
                                        'Could not force discharge.')
            if not ec.has_cros_ec():
                raise error.TestNAError('DUT does not have CrOS EC. '
                                        'Could not force discharge.')
            if not power_utils.charge_control_by_ectool(False):
                raise error.TestError('Could not run battery force discharge.')

        self._psr = power_utils.DisplayPanelSelfRefresh()
        self._services = service_stopper.ServiceStopper(
                service_stopper.ServiceStopper.POWER_DRAW_SERVICES)
        self._services.stop_services()
        self._stats = power_status.StatoMatic()

        self._meas_logs = power_status.create_measurement_loggers(
                seconds_period, self._checkpoint_logger)

        self._pdash_note = pdash_note

    def warmup(self, warmup_time=30):
        """Warm up.

        Wait between initialization and run_once for new settings to stabilize.

        @param warmup_time: integer of seconds to warmup.
        """
        time.sleep(warmup_time)

    def start_measurements(self):
        """Start measurements."""
        for log in self._meas_logs:
            log.start()
        self._start_time = time.time()
        if self.status.battery:
            self._start_energy = self.status.battery.energy
        power_telemetry_utils.start_measurement()

    def loop_sleep(self, loop, sleep_secs):
        """Jitter free sleep.

        @param loop: integer of loop (1st is zero).
        @param sleep_secs: integer of desired sleep seconds.
        """
        next_time = self._start_time + (loop + 1) * sleep_secs
        time.sleep(next_time - time.time())

    def checkpoint_measurements(self, name, start_time=None):
        """Checkpoint measurements.

        @param name: string name of measurement being checkpointed.
        @param start_time: float of time in seconds since Epoch that
                measurements being checkpointed began.
        """
        if not start_time:
            start_time = self._start_time
        self.status.refresh()
        self._checkpoint_logger.checkpoint(name, start_time)
        self._psr.refresh()

    def collect_keypress_latency(self, cr):
        """Collect keypress latency information from Histograms.

        @param cr: object, the Chrome instance
        """

        keypress_histogram_end = histogram_verifier.get_histogram(
            cr, self.keypress_histogram)
        logger = power_dashboard.KeyvalLogger(self._start_time, time.time())
        matches = re.search((self.histogram_re % self.keypress_histogram),
                            keypress_histogram_end)

        if matches:
            count = int(matches.group(1))
            mean_latency = float(matches.group(2))
            logging.info('latency count %d mean %f', count, mean_latency)
            self.keyvals['keypress_cnt'] = count
            self.keyvals['keypress_latency_us_avg'] = mean_latency
            self.output_perf_value(description='keypress_cnt', value=count,
                                   higher_is_better=True)
            self.output_perf_value(description='keypress_latency_us_avg',
                                   value=mean_latency,
                                   higher_is_better=False)
            logger.add_item('keypress_cnt', count, 'point', 'keypress')
            logger.add_item('keypress_latency_us_avg', mean_latency, 'point',
                            'keypress')

        # Capture the first bucket >= 90th percentile
        for s in keypress_histogram_end.splitlines():
            matches = re.search((self.hist_percentile_re), s)
            if matches:
                lat = int(matches.group(1))
                perc = int(matches.group(2))
                if perc >= 90:
                    self.keyvals['keypress_latency_us_high'] = lat
                    self.keyvals['keypress_high_percentile'] = perc
                    self.output_perf_value(
                        description='keypress_latency_us_high', value=lat,
                        higher_is_better=False)
                    self.output_perf_value(
                        description='keypress_high_percentile', value=perc,
                        higher_is_better=False)
                    logger.add_item('keypress_latency_us_high', lat, 'point',
                                    'keypress')
                    logger.add_item('keypress_high_percentile', perc, 'point',
                                    'keypress')
                    break

        self._meas_logs.append(logger)

    def publish_keyvals(self):
        """Publish power result keyvals."""
        keyvals = self._stats.publish()
        keyvals['level_backlight_max'] = self.backlight.get_max_level()
        keyvals['level_backlight_current'] = self.backlight.get_level()

        # record battery stats if not on AC
        if not self._force_discharge and self.status.on_ac():
            keyvals['b_on_ac'] = 1
        else:
            keyvals['b_on_ac'] = 0

        if self.status.battery:
            keyvals['ah_charge_full'] = self.status.battery.charge_full
            keyvals['ah_charge_full_design'] = \
                                self.status.battery.charge_full_design
            keyvals['ah_charge_now'] = self.status.battery.charge_now
            keyvals['a_current_now'] = self.status.battery.current_now
            keyvals['wh_energy'] = self.status.battery.energy
            energy_used = self._start_energy - self.status.battery.energy
            runtime_minutes = (time.time() - self._start_time) / 60.
            keyvals['wh_energy_used'] = energy_used
            keyvals['minutes_tested'] = runtime_minutes
            if energy_used > 0 and runtime_minutes > 1:
                keyvals['w_energy_rate'] = energy_used * 60 / runtime_minutes
            keyvals['v_voltage_min_design'] = \
                                self.status.battery.voltage_min_design
            keyvals['v_voltage_now'] = self.status.battery.voltage_now

        for log in self._meas_logs:
            keyvals.update(log.calc())
        keyvals.update(self._psr.get_keyvals())

        self.keyvals.update(keyvals)

        core_keyvals = power_utils.get_core_keyvals(self.keyvals)
        self.write_perf_keyval(core_keyvals)

    def publish_dashboard(self):
        """Report results to chromeperf & power dashboard."""

        self.publish_keyvals()

        # publish power values
        for key, values in self.keyvals.iteritems():
            if key.endswith('pwr_avg'):
                self.output_perf_value(description=key, value=values, units='W',
                        higher_is_better=False, graph='power')

        # publish temperature values
        for key, values in self.keyvals.iteritems():
            if key.endswith('temp_avg'):
                self.output_perf_value(description=key, value=values, units='C',
                        higher_is_better=False, graph='temperature')

        # publish fps values
        for key, values in self.keyvals.iteritems():
            if key.endswith('fps_avg'):
                self.output_perf_value(description=key, value=values,
                        units='fps', higher_is_better=True, graph='fps')

        # publish to power dashboard
        dashboard_factory = power_dashboard.get_dashboard_factory()
        for log in self._meas_logs:
            dashboard = dashboard_factory.createDashboard(log,
                self.tagged_testname, self.resultsdir, note=self._pdash_note)
            dashboard.upload()

    def _save_results(self):
        """Save results of each logger in resultsdir."""
        for log in self._meas_logs:
            log.save_results(self.resultsdir)
        self._checkpoint_logger.save_checkpoint_data(self.resultsdir)

    def postprocess_iteration(self):
        """Write keyval and send data to dashboard."""
        power_telemetry_utils.end_measurement()
        self.status.refresh()
        for log in self._meas_logs:
            log.done = True
        super(power_Test, self).postprocess_iteration()
        self.publish_dashboard()
        self._save_results()

    def cleanup(self):
        """Reverse setting change in initialization."""
        if self._force_discharge:
            if not power_utils.charge_control_by_ectool(True):
                logging.warn('Can not restore from force discharge.')
        if self.backlight:
            self.backlight.restore()
        self._services.restore_services()
        super(power_Test, self).cleanup()

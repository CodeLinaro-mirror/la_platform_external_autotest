# Copyright 2016 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import sys
import functools
import logging
import time

import common
from autotest_lib.client.common_lib import hosts
from autotest_lib.client.common_lib import utils
from autotest_lib.server.cros.servo import servo
from autotest_lib.server.hosts import repair_utils

try:
    from chromite.lib import metrics
except ImportError:
    metrics = utils.metrics_mock

def ignore_exception_for_non_cros_host(func):
    """
    Decorator to ignore ControlUnavailableError if servo host is not cros host.
    When using test_that command on a workstation, this enables usage of
    additional servo devices such as servo micro and Sweetberry. This shall not
    change any lab behavior.
    """
    @functools.wraps(func)
    def wrapper(self, host):
        """
        Wrapper around func.
        """
        try:
            func(self, host)
        except servo.ControlUnavailableError as e:
            if host.is_cros_host():
                raise
            logging.warning("Servo host is not cros host, ignore %s: %s",
                            type(e).__name__, e)
    return wrapper


class _UpdateVerifier(hosts.Verifier):
    """
    Verifier to trigger a servo host update, if necessary.

    The operation doesn't wait for the update to complete and is
    considered a success whether or not the servo is currently
    up-to-date.
    """

    def verify(self, host):
        # First, only run this verifier if the host is in the physical lab.
        # Secondly, skip if the test is being run by test_that, because subnet
        # restrictions can cause the update to fail.
        try:
            if host.is_labstation():
                logging.info("Skip update check because the host is a"
                             " labstation and labstation update is handled"
                             " by labstation AdminRepair task.")
                return
            if host.is_in_lab() and host.job and host.job.in_lab:
                # We have seen cases that invalid GPT headers/entries block
                # v3s from been update, so always try to repair here.
                # See crbug.com/994396, crbug.com/1057302.
                host.run('cgpt repair /dev/mmcblk0', ignore_status=True)

                host.update_image(wait_for_update=False)
        # We don't want failure from update block DUT repair action.
        # See crbug.com/1029950.
        except Exception as e:
            raise hosts.AutoservNonCriticalVerifyError, e.message, sys.exc_info()[2]

    @property
    def description(self):
        return 'servo host software is up-to-date'


class _ConfigVerifier(hosts.Verifier):
    """
    Base verifier for the servo config file verifiers.
    """

    CONFIG_FILE = '/var/lib/servod/config'
    ATTR = ''

    @staticmethod
    def _get_config_val(host, config_file, attr):
        """
        Get the `attr` for `host` from `config_file`.

        @param host         Host to be checked for `config_file`.
        @param config_file  Path to the config file to be tested.
        @param attr         Attribute to get from config file.

        @return The attr val as set in the config file, or `None` if
                the file was absent.
        """
        getboard = ('CONFIG=%s ; [ -f $CONFIG ] && '
                    '. $CONFIG && echo $%s' % (config_file, attr))
        attr_val = host.run(getboard, ignore_status=True).stdout
        return attr_val.strip('\n') if attr_val else None

    @staticmethod
    def _validate_attr(host, val, expected_val, attr, config_file):
        """
        Check that the attr setting is valid for the host.

        This presupposes that a valid config file was found.  Raise an
        execption if:
          * There was no attr setting from the file (i.e. the setting
            is an empty string), or
          * The attr setting is valid, the attr is known,
            and the setting doesn't match the DUT.

        @param host         Host to be checked for `config_file`.
        @param val          Value to be tested.
        @param expected_val Expected value.
        @param attr         Attribute we're validating.
        @param config_file  Path to the config file to be tested.
        """
        if not val:
            raise hosts.AutoservVerifyError(
                    'config file %s exists, but %s '
                    'is not set' % (attr, config_file))
        if expected_val is not None and val != expected_val:
            raise hosts.AutoservVerifyError(
                    '%s is %s; it should be %s' % (attr, val, expected_val))


    def _get_config(self, host):
        """
        Return the config file to check.

        @param host     Host object.

        @return The config file to check.
        """
        return '%s_%d' % (self.CONFIG_FILE, host.servo_port)

    @property
    def description(self):
        return 'servo %s setting is correct' % self.ATTR


class _SerialConfigVerifier(_ConfigVerifier):
    """
    Verifier for the servo SERIAL configuration.
    """

    ATTR = 'SERIAL'

    def verify(self, host):
        """
        Test whether the `host` has a `SERIAL` setting configured.

        This tests the config file names used by the `servod` upstart
        job for a valid setting of the `SERIAL` variable.  The following
        conditions raise errors:
          * The SERIAL setting doesn't match the DUT's entry in the AFE
            database.
          * There is no config file.
        """
        if not host.is_cros_host():
            return
        # Not all servo hosts will have a servo serial so don't verify if it's
        # not set.
        if host.servo_serial is None:
            return
        config = self._get_config(host)
        serialval = self._get_config_val(host, config, self.ATTR)
        if serialval is None:
            raise hosts.AutoservVerifyError(
                    'Servo serial is unconfigured; should be %s'
                    % host.servo_serial
            )

        self._validate_attr(host, serialval, host.servo_serial, self.ATTR,
                            config)



class _BoardConfigVerifier(_ConfigVerifier):
    """
    Verifier for the servo BOARD configuration.
    """

    ATTR = 'BOARD'

    def verify(self, host):
        """
        Test whether the `host` has a `BOARD` setting configured.

        This tests the config file names used by the `servod` upstart
        job for a valid setting of the `BOARD` variable.  The following
        conditions raise errors:
          * A config file exists, but the content contains no setting
            for BOARD.
          * The BOARD setting doesn't match the DUT's entry in the AFE
            database.
          * There is no config file.
        """
        if not host.is_cros_host():
            return
        config = self._get_config(host)
        boardval = self._get_config_val(host, config, self.ATTR)
        if boardval is None:
            msg = 'Servo board is unconfigured'
            if host.servo_board is not None:
                msg += '; should be %s' % host.servo_board
            raise hosts.AutoservVerifyError(msg)

        self._validate_attr(host, boardval, host.servo_board, self.ATTR,
                            config)


class _ServodJobVerifier(hosts.Verifier):
    """
    Verifier to check that the `servod` upstart job is running.
    """

    def verify(self, host):
        if not host.is_cros_host():
            return
        status_cmd = 'status servod PORT=%d' % host.servo_port
        job_status = host.run(status_cmd, ignore_status=True).stdout
        if 'start/running' not in job_status:
            raise hosts.AutoservVerifyError(
                    'servod not running on %s port %d' %
                    (host.hostname, host.servo_port))

    @property
    def description(self):
        return 'servod upstart job is running'


class _DiskSpaceVerifier(hosts.Verifier):
    """
    Verifier to make sure there is enough disk space left on servohost.
    """

    def verify(self, host):
        # Check available space of stateful is greater than threshold, in Gib.
        host.check_diskspace('/mnt/stateful_partition', 0.5)

    @property
    def description(self):
        return 'servohost has enough disk space.'


class _ServodConnectionVerifier(hosts.Verifier):
    """
    Verifier to check that we can connect to servod server.

    If this verifier failed, it most likely servod was crashed or in a
    crashing loop. For servo_v4 it's usually caused by not able to detect
    CCD or servo_micro.
    """

    def verify(self, host):
        host.initilize_servo()

    @property
    def description(self):
        return 'servod service is taking calls'


class _ServodControlVerifier(hosts.Verifier):
    """
    Verifier to check basic servo control functionality.

    This tests the connection to the target servod service with a simple
    method call.  As a side-effect, all servo signals are initialized to
    default values.

    N.B. Initializing servo signals is necessary because the power
    button and lid switch verifiers both test against expected initial
    values.
    """

    def verify(self, host):
        try:
            host.initialize_dut_for_servo()
        except Exception as e:
            raise hosts.AutoservNonCriticalVerifyError, e.message, sys.exc_info()[2]

    @property
    def description(self):
        return 'Basic servod control is working'


class _CCDTestlabVerifier(hosts.Verifier):
    """
    Verifier to check that ccd testlab is anabled.

    ALl DUT connected by ccd has to supported cr50 with enabled testlab
    to allow manipulation by servo. The flag testlab is sticky and will
    stay enabled if was set up. To enable testlab ccd has to be open.
    """
    @ignore_exception_for_non_cros_host
    def verify(self, host):
        if not host.get_servo().has_control('cr50_testlab'):
            raise hosts.AutoservVerifyError(
                'cr50 has to be supported when use servo with'
                ' ccd_cr50/type-c connection')

        status = host.get_servo().get('cr50_testlab')
        if status != 'on':
            data = {'port': host.servo_port,
                    'host': host.get_dut_hostname() or host.hostname,
                    'board': host.servo_board or ''}
            metrics.Counter(
                'chromeos/autotest/repair/ccd_testlab').increment(fields=data)
            # TODO enable when lab will finished rework on all DUTs
            # or new servo_state will come to the stage
            # raise hosts.AutoservNonCriticalVerifyError(
            #     'The ccd testlab is off (not enabled);'
            #     ' required the rework to enable it (go/ccd-setup)',
            #     'ccd_testlab_disabled')
            host.record('INFO', None, 'ccd_testlab_disabled',
                        'The ccd testlab is off (not enabled);'
                        ' required the rework to enable it (go/ccd-setup)')

    def _is_applicable(self, host):
        if host.get_servo():
            # Only when DUT connect by type-c.
            return host.get_servo().get_main_servo_device() == 'ccd_cr50'
        return False

    @property
    def description(self):
        return 'ccd testlab enabled'

class _CCDPowerDeliveryVerifier(hosts.Verifier):
    """Verifier to check and reset servo_v4_role for servos that support
    power delivery feature(a.k.a power pass through).

    There are currently two position of servo_v4_role, src and snk:
    src --  servo in power delivery mode and passes power to the DUT.
    snk --  servo in normal mode and not passes power to DUT.
    We want to ensure that servo_v4_role is set to src.

    TODO(xianuowang@) Convert it to verifier/repair action pair or remove it
    once we collected enough metrics.
    """
    # Change to use the  constant value in CrosHost if we move it to
    # verifier/repair pair.
    CHANGE_SERVO_ROLE_TIMEOUT = 180

    def verify(self, host):
        if host.get_servo().get('servo_v4_role') == 'snk':
            logging.warning('The servo initlized with role snk while'
                            ' supporting power delivery, resetting role'
                            ' to src...')
            host.get_servo().set_servo_v4_role('src')
            time.sleep(self.CHANGE_SERVO_ROLE_TIMEOUT)
            result = host.get_servo().get('servo_v4_role')
            logging.debug('Servo_v4 role after reset: %s', result)
            metrics_data = {
                'hostname': host.get_dut_hostname() or 'unknown',
                'status': 'success' if result == 'src' else 'failed',
                'board': host.servo_board or 'unknown',
                'model': host.servo_model or 'unknown'
            }
            metrics.Counter(
                'chromeos/autotest/repair/verifier/power_delivery3'
            ).increment(fields=metrics_data)

    def _is_applicable(self, host):
        return (host.is_in_lab() and
                host.get_servo().supports_built_in_pd_control())

    @property
    def description(self):
        return 'ensure applicable servo is in "src" mode for power delivery'


class _PowerButtonVerifier(hosts.Verifier):
    """
    Verifier to check sanity of the `pwr_button` signal.

    Tests that the `pwr_button` signal shows the power button has been
    released.  When `pwr_button` is stuck at `press`, it commonly
    indicates that the ribbon cable is disconnected.
    """
    # TODO (crbug.com/646593) - Remove list below once servo has been updated
    # with a dummy pwr_button signal.
    _BOARDS_WO_PWR_BUTTON = ['arkham', 'gale', 'mistral', 'storm', 'whirlwind']

    @ignore_exception_for_non_cros_host
    def verify(self, host):
        if host.servo_board in self._BOARDS_WO_PWR_BUTTON:
            return
        try:
            button = host.get_servo().get('pwr_button')
        except Exception as e:
            raise hosts.AutoservNonCriticalVerifyError, e.message, sys.exc_info()[2]

        if button != 'release':
            raise hosts.AutoservNonCriticalVerifyError(
                'Check ribbon cable: \'pwr_button\' is stuck')


    @property
    def description(self):
        return 'pwr_button control is normal'


class _LidVerifier(hosts.Verifier):
    """
    Verifier to check sanity of the `lid_open` signal.
    """

    @ignore_exception_for_non_cros_host
    def verify(self, host):
        try:
            lid_open = host.get_servo().get('lid_open')
        except Exception as e:
            raise hosts.AutoservNonCriticalVerifyError, e.message, sys.exc_info()[2]

        if lid_open != 'yes' and lid_open != 'not_applicable':
            raise hosts.AutoservNonCriticalVerifyError(
                'Check lid switch: lid_open is %s' % lid_open)

    @property
    def description(self):
        return 'lid_open control is normal'


class _EcBoardVerifier(hosts.Verifier):
    """
    Verifier response from the 'ec_board' control.
    """

    @ignore_exception_for_non_cros_host
    def verify(self, host):
        if host.is_ec_supported():
            ec_board_name = ''
            try:
                ec_board_name = host.get_servo().get_ec_board()
                logging.debug('EC board: %s', ec_board_name)
            except Exception as e:
                raise hosts.AutoservNonCriticalVerifyError(
                        '`ec_board` control is not responding; '
                        'may be caused of broken EC firmware')
        else:
            logging.info('The board not support EC')

    @property
    def description(self):
        return 'Check EC by get `ec_board` control'


class _RestartServod(hosts.RepairAction):
    """Restart `servod` with the proper BOARD setting."""

    def repair(self, host):
        if not host.is_cros_host():
            raise hosts.AutoservRepairError(
                    'Can\'t restart servod: not running '
                    'embedded Chrome OS.',
                    'servo_not_applicable_to_non_cros_host')
        host.restart_servod()

    @property
    def description(self):
        return 'Start servod with the proper config settings.'


class _ServoRebootRepair(repair_utils.RebootRepair):
    """
    Reboot repair action that also waits for an update.

    This is the same as the standard `RebootRepair`, but for
    a non-multi-DUTs servo host, if there's a pending update,
    we wait for that to complete before rebooting.  This should
    ensure that the servo_v3 is up-to-date after reboot. Labstation
    reboot and update is handled by labstation host class.
    """

    def repair(self, host):
        if host.is_localhost() or not host.is_cros_host():
            raise hosts.AutoservRepairError(
                'Target servo is not a test lab servo',
                'servo_not_applicable_to_host_outside_lab')
        if host.is_labstation():
            host.request_reboot()
            logging.warning('Reboot labstation requested, it will be '
                            'handled by labstation AdminRepair task.')
        else:
            try:
                host.update_image(wait_for_update=True)
            # We don't want failure from update block DUT repair action.
            # See crbug.com/1029950.
            except Exception as e:
                logging.error('Failed to update servohost image: %s', e)
            super(_ServoRebootRepair, self).repair(host)

    @property
    def description(self):
        return 'Wait for update, then reboot servo host.'


class _ECRebootRepair(hosts.RepairAction):
    """
    Reboot EC on DUT from servo.
    """

    def _is_applicable(self, host):
        return (not host.is_localhost()) and host.is_ec_supported()

    def repair(self, host):
        host.get_servo().ec_reboot()

    @property
    def description(self):
        return 'Reboot EC'


class _DutRebootRepair(hosts.RepairAction):
    """
    Reboot DUT to recover some servo controls depending on EC console.

    Some servo controls, like lid_open, requires communicating with DUT through
    EC UART console. Failure of this kinds of controls can be recovered by
    rebooting the DUT.
    """

    def repair(self, host):
        host.get_servo().get_power_state_controller().reset()
        # Get the lid_open value which requires EC console.
        lid_open = host.get_servo().get('lid_open')
        if lid_open != 'yes' and lid_open != 'not_applicable':
            raise hosts.AutoservVerifyError(
                    'Still fail to contact EC console after rebooting DUT')

    @property
    def description(self):
        return 'Reset the DUT via servo'


class _DiskCleanupRepair(hosts.RepairAction):
    """
    Remove old logs/metrics/crash_dumps on servohost to free up disk space.
    """
    KEEP_LOGS_MAX_DAYS = 5

    FILE_TO_REMOVE = ['/var/lib/metrics/uma-events',
                      '/var/spool/crash/*']

    def repair(self, host):
        if host.is_localhost():
            # we don't want to remove anything from local testing.
            return

        # Remove old servod logs.
        host.run('/usr/bin/find /var/log/servod_* -mtime +%d -print -delete'
                 % self.KEEP_LOGS_MAX_DAYS, ignore_status=True)

        # Remove pre-defined metrics and crash dumps.
        for path in self.FILE_TO_REMOVE:
            host.run('rm %s' % path, ignore_status=True)

    @property
    def description(self):
        return 'Clean up old logs/metrics on servohost to free up disk space.'


def create_servo_repair_strategy():
    """
    Return a `RepairStrategy` for a `ServoHost`.
    """
    config = ['brd_config', 'ser_config']
    verify_dag = [
        (repair_utils.SshVerifier,   'servo_ssh',   []),
        (_DiskSpaceVerifier,         'disk_space',  ['servo_ssh']),
        (_UpdateVerifier,            'update',      ['servo_ssh']),
        (_BoardConfigVerifier,       'brd_config',  ['servo_ssh']),
        (_SerialConfigVerifier,      'ser_config',  ['servo_ssh']),
        (_ServodJobVerifier,         'servod_job',   config + ['disk_space']),
        (_ServodConnectionVerifier,  'servod_connection', ['servod_job']),
        (_ServodControlVerifier,     'servod_control', ['servod_connection']),
        (_PowerButtonVerifier,       'pwr_button',  ['servod_connection']),
        (_LidVerifier,               'lid_open',    ['servod_connection']),
        (_EcBoardVerifier,           'ec_board',    ['servod_connection']),
        (_CCDTestlabVerifier,        'ccd_testlab', ['servod_connection']),
        (_CCDPowerDeliveryVerifier,  'power_delivery', ['servod_connection']),
    ]

    servod_deps = ['servod_job', 'servod_connection', 'servod_control',
                   'pwr_button']
    repair_actions = [
        (_DiskCleanupRepair, 'disk_cleanup', ['servo_ssh'], ['disk_space']),
        (_RestartServod, 'restart', ['servo_ssh'], config + servod_deps),
        (_ServoRebootRepair, 'servo_reboot', ['servo_ssh'], servod_deps),
        (
            _DutRebootRepair, 'dut_reboot', ['servod_connection'],
            ['servod_control', 'lid_open', 'ec_board']
        ),
        (
            _ECRebootRepair, 'ec_reboot', ['servod_connection'],
            ['servod_control', 'lid_open', 'ec_board']
        ),
    ]
    return hosts.RepairStrategy(verify_dag, repair_actions, 'servo')

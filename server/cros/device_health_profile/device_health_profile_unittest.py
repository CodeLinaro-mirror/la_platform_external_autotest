#!/usr/bin/python2
# pylint: disable=missing-docstring

import time
import unittest
import mock

import common
from autotest_lib.server.cros.device_health_profile import device_health_profile
from autotest_lib.server.cros.device_health_profile import profile_constants


class MockHostInfoStore(object):
    def __init__(self):
        self.board = 'mock_board'
        self.model = 'mock_model'


class MockHost(object):
    def __init__(self, hostname):
        self.hostname = hostname
        self.host_info_store = mock.Mock()
        self.host_info_store.get.return_value = MockHostInfoStore()
        self.job = None

    def check_cached_up_status(self):
        return True

    def is_up(self):
        return True

    def send_file(self, source, dest):
        return True

    def get_file(self, source, dest):
        return True

    def is_file_exists(self, file_path):
        return False


def create_device_health_profile():
    dut = MockHost('dummy_dut_hostname')
    servohost = MockHost('dummy_servohost_hostname')
    return device_health_profile.DeviceHealthProfile(dut, servohost)


class DeviceHealthProfileTestCase(unittest.TestCase):
    dhp = create_device_health_profile()

    def test_validate_device_health_profile_data(self):
        profile_data = self.dhp.health_profile
        self.assertEqual(self.dhp._validate_profile_data(profile_data), True)

    def test_get_board(self):
        self.assertEqual(self.dhp.get_board(), 'mock_board')

    def test_get_model(self):
        self.assertEqual(self.dhp.get_model(), 'mock_model')

    def test_get_profile_version(self):
        self.assertEqual(self.dhp.get_profile_version(),
                         profile_constants.PROFILE_VERSION)

    def test_dut_state(self):
        self.assertEqual(self.dhp.get_dut_state(),
                         profile_constants.DEFAULT_STRING_VALUE)
        self.dhp.update_dut_state('test_state_1')
        self.assertEqual(self.dhp.get_dut_state(), 'test_state_1')

    def test_servo_state(self):
        self.assertEqual(self.dhp.get_servo_state(),
                         profile_constants.DEFAULT_STRING_VALUE)
        self.dhp.update_servo_state('servod_issue')
        self.assertEqual(self.dhp.get_servo_state(), 'servod_issue')

    def test_cros_stable_version(self):
        self.assertEqual(self.dhp.get_cros_stable_version(),
                         profile_constants.DEFAULT_STRING_VALUE)
        self.dhp.set_cros_stable_version('dummy-release/R80-10000.0.0')
        self.assertEqual(self.dhp.get_cros_stable_version(),
                         'dummy-release/R80-10000.0.0')

    def test_firmware_stable_version(self):
        self.assertEqual(self.dhp.get_firmware_stable_version(),
                         profile_constants.DEFAULT_STRING_VALUE)
        self.dhp.set_firmware_stable_version('dummy_firmware_release')
        self.assertEqual(self.dhp.get_firmware_stable_version(),
                         'dummy_firmware_release')

    def test_last_update_time(self):
        cached_time = self.dhp.get_last_update_time()
        self.assertRegexpMatches(cached_time, r'\d{4}[-/]\d{2}[-/]\d{2}')
        # Sleep 1 second so updated timestamp is different than current one.
        time.sleep(1)
        self.dhp.refresh_update_time()
        self.assertNotEqual(cached_time, self.dhp.get_last_update_time())

    def test_last_update_time_epoch(self):
        cached_time_epoch = self.dhp.get_last_update_time_epoch()
        self.assertEqual(type(cached_time_epoch), int)
        # Sleep 1 second so updated timestamp is different than current one.
        time.sleep(1)
        self.dhp.refresh_update_time()
        self.assertGreater(self.dhp.get_last_update_time_epoch(),
                           cached_time_epoch)

    def test_enter_current_state_time(self):
        cached_time = self.dhp.get_enter_current_state_time()
        self.assertRegexpMatches(cached_time, r'\d{4}[-/]\d{2}[-/]\d{2}')
        # Sleep 1 second so updated timestamp is different than current one.
        time.sleep(1)
        self.dhp.update_dut_state('test_state_2')
        self.assertNotEqual(cached_time,
                            self.dhp.get_enter_current_state_time())

    def test_enter_current_state_time_epoch(self):
        cached_time_epoch = self.dhp.get_enter_current_state_time_epoch()
        self.assertEqual(type(cached_time_epoch), int)
        # Sleep 1 second so updated timestamp is different than current one.
        time.sleep(1)
        self.dhp.update_dut_state('test_state_3')
        self.assertGreater(self.dhp.get_enter_current_state_time_epoch(),
                           cached_time_epoch)

    def test_repair_fail_count(self):
        cached_count = self.dhp.get_repair_fail_count()
        self.dhp.increase_repair_fail_count()
        self.assertEqual(self.dhp.get_repair_fail_count(), cached_count + 1)

    def test_provision_fail_count(self):
        cached_count = self.dhp.get_provision_fail_count()
        self.dhp.increase_provision_fail_count()
        self.assertEqual(self.dhp.get_provision_fail_count(), cached_count + 1)

    def test_failed_verifiers(self):
        tag = 'dummy_verifier'
        self.assertEqual(self.dhp.get_failed_verifiers(), {})
        self.assertEqual(self.dhp.get_failed_verifier(tag), 0)
        self.dhp.insert_failed_verifier(tag)
        self.assertEqual(self.dhp.get_failed_verifier(tag), 1)
        self.assertEqual(self.dhp.get_failed_verifiers(),
                         {'dummy_verifier': 1})

    def test_succeed_repair_action(self):
        tag = 'dummy_succeed_action'
        self.assertEqual(self.dhp.get_succeed_repair_actions(), {})
        self.assertEqual(self.dhp.get_succeed_repair_action(tag), 0)
        self.dhp.insert_succeed_repair_action(tag)
        self.assertEqual(self.dhp.get_succeed_repair_action(tag), 1)
        self.assertEqual(self.dhp.get_succeed_repair_actions(),
                         {'dummy_succeed_action': 1})

    def test_failed_repair_action(self):
        tag = 'dummy_failed_action'
        self.assertEqual(self.dhp.get_failed_repair_actions(), {})
        self.assertEqual(self.dhp.get_failed_repair_action(tag), 0)
        self.dhp.insert_failed_repair_action(tag)
        self.assertEqual(self.dhp.get_failed_repair_action(tag), 1)
        self.assertEqual(self.dhp.get_failed_repair_actions(),
                         {'dummy_failed_action': 1})


if __name__ == '__main__':
    unittest.main()

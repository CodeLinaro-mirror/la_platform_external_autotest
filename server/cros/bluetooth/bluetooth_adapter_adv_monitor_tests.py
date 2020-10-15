# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Server side bluetooth tests on Advertisement Monitor API"""

import time
import logging
import array

from autotest_lib.client.bin import utils
from autotest_lib.server.cros.bluetooth import bluetooth_adapter_tests


class TestMonitor():
    """Local object hosting the test values for Advertisement Monitor object.

    This class holds the values of parameters for creating an Advertisement
    Monitor object.

    """

    # Index of the pattern data in the patterns filter.
    PATTERN_DATA_IDX = 2

    def __init__(self, app_id):
        """Construction of a local monitor object.

        @param app_id: the app id associated with the monitor.

        """
        self.type = None
        self.rssi = []
        self.patterns = []
        self.monitor_id = None
        self.app_id = app_id


    def _bytes(self, str_data):
        """Convert string data to byte array.

        @param str_data: the string data.

        @returns: the byte array.

        """
        return [b for b in array.array('B', str_data)]


    def update_type(self, monitor_type):
        """Update the monitor type.

        @param monitor_type: type of the monitor.

        """
        self.type = monitor_type


    def update_rssi(self, monitor_rssi):
        """Update the RSSI filter values.

        @param rssi: the list of rssi threshold and timeout values.

        """
        self.rssi = monitor_rssi


    def update_patterns(self, monitor_patterns):
        """Update the content filter patterns.

        @param patterns: the list of start position, ad type and patterns.

        """
        # Convert string patterns to byte array, if any.
        for pattern in monitor_patterns:
            if isinstance(pattern[self.PATTERN_DATA_IDX], str):
                pattern[self.PATTERN_DATA_IDX] = self._bytes(
                        pattern[self.PATTERN_DATA_IDX])

        self.patterns = monitor_patterns


    def update_monitor_id(self, monitor_id):
        """Store the monitor id returned by add_monitor().

        @param monitor_id: the monitor id.

        """
        self.monitor_id = monitor_id


    def get_monitor_data(self):
        """Return the monitor parameters.

        @returns: List containing the monitor data.

        """
        return [self.type, self.rssi, self.patterns]


    def get_monitor_id(self):
        """Return the monitor id.

        @returns: monitor id if monitor is already added, None otherwise.

        """
        return self.monitor_id


    def get_app_id(self):
        """Return the application id.

        @returns: app id associated to the monitor object.

        """
        return self.app_id


class BluetoothAdapterAdvMonitorTests(
        bluetooth_adapter_tests.BluetoothAdapterTests):
    """Server side bluetooth adapter advertising Test.

    This class comprises a number of test cases to verify bluetooth
    Advertisement Monitor API.

    Refer to the test plan doc for more details: go/bt-advmon-api-test-plan

    """

    ADD_MONITOR_POLLING_TIMEOUT_SECS = 3
    ADD_MONITOR_POLLING_SLEEP_SECS = 1
    PAIR_TEST_SLEEP_SECS = 5

    # Non-zero count value is used to indicate the case where multiple
    # DeviceFound/DeviceLost events are expected to occur.
    MULTIPLE_EVENTS = -1

    test_case_log = bluetooth_adapter_tests.test_case_log
    test_retry_and_log = bluetooth_adapter_tests.test_retry_and_log


    def read_supported_types(self):
        """Read the Advertisement Monitor supported monitor types.

        @returns: List of supported advertisement monitor types.

        """
        return self.bluetooth_facade.advmon_read_supported_types()


    def read_supported_features(self):
        """Read the Advertisement Monitor supported features.

        @returns: List of supported advertisement monitor features.

        """
        return self.bluetooth_facade.advmon_read_supported_features()


    def create_app(self):
        """Create an advertisement monitor app.

        @returns: app id, once the app is created.

        """
        return self.bluetooth_facade.advmon_create_app()


    def exit_app(self, app_id):
        """Exit an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_exit_app(app_id)


    def kill_app(self, app_id):
        """Kill an advertisement monitor app by sending SIGKILL.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_kill_app(app_id)


    def register_app(self, app_id):
        """Register an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_register_app(app_id)


    def unregister_app(self, app_id):
        """Unregister an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_unregister_app(app_id)


    def add_monitor(self, app_id, monitor_data):
        """Create an Advertisement Monitor object.

        @param app_id: the app id.
        @param monitor_data: the list containing monitor type, RSSI filter
                             values and patterns.

        @returns: monitor id, once the monitor is created, None otherwise.

        """
        return self.bluetooth_facade.advmon_add_monitor(app_id, monitor_data)


    def remove_monitor(self, app_id, monitor_id):
        """Remove the Advertisement Monitor object.

        @param app_id: the app id.
        @param monitor_id: the monitor id.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_remove_monitor(app_id, monitor_id)


    def get_event_count(self, app_id, monitor_id, event='All'):
        """Read the count of a particular event on the given monitor.

        @param app_id: the app id.
        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: count of the specific event or dict of counts of all events.

        """
        return self.bluetooth_facade.advmon_get_event_count(app_id,
                                                            monitor_id,
                                                            event)


    def reset_event_count(self, app_id, monitor_id, event='All'):
        """Reset the count of a particular event on the given monitor.

        @param app_id: the app id.
        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: True on success, False otherwise.

        """
        return self.bluetooth_facade.advmon_reset_event_count(app_id,
                                                              monitor_id,
                                                              event)


    @test_retry_and_log(False)
    def test_supported_types(self):
        """Test supported monitor types.

        @returns: True on success, False otherwise.

        """
        supported_types = self.read_supported_types()
        for supported_type in supported_types:
            logging.info('type: %s', supported_type)

        # TODO(b/169658213) - add check for supported types.
        return True


    @test_retry_and_log(False)
    def test_supported_features(self):
        """Test supported features.

        @returns: True on success, False otherwise.

        """
        supported_features = self.read_supported_features()
        for supported_feature in supported_features:
            logging.info('feature: %s', supported_feature)

        # TODO(b/169658213) - add check for supported features.
        return True


    @test_retry_and_log(False)
    def test_exit_app(self, app_id):
        """Test exit application.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.exit_app(app_id)


    @test_retry_and_log(False)
    def test_kill_app(self, app_id):
        """Test kill application.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        return self.kill_app(app_id)


    @test_retry_and_log(False)
    def test_register_app(self, app_id, expected=True):
        """Test register application.

        @param app_id: the app id.
        @param expected: expected result of the RegisterMonitor method.

        @returns: True on success, False otherwise.

        """
        return self.register_app(app_id) == expected


    @test_retry_and_log(False)
    def test_unregister_app(self, app_id, expected=True):
        """Test unregister application.

        @param app_id: the app id.
        @param expected: expected result of the UnregisterMonitor method.

        @returns: True on success, False otherwise.

        """
        return self.unregister_app(app_id) == expected


    @test_retry_and_log(False)
    def test_monitor_activate(self, monitor, expected):
        """Test if the Activate method on the monitor has been invoked or not.

        @param monitor: the local monitor object.
        @param expected: expected state of the Activate event.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = monitor.get_monitor_id()
        if monitor_id is None:
            return False

        def _check_activate():
            """Handler for the activate event."""
            return self.get_event_count(app_id, monitor_id, 'Activate') == 1

        activated = False
        try:
            utils.poll_for_condition(
                    condition=_check_activate,
                    timeout=self.ADD_MONITOR_POLLING_TIMEOUT_SECS,
                    sleep_interval=self.ADD_MONITOR_POLLING_SLEEP_SECS,
                    desc='Waiting for activate')
            activated = True
        except utils.TimeoutError as e:
            logging.error('activate: %s', e)
        except:
            logging.error('activate: unexpected error')

        return expected == activated


    @test_retry_and_log(False)
    def test_monitor_release(self, monitor, expected):
        """Test if the Release method on the monitor has been invoked or not.

        @param monitor: the local monitor object.
        @param expected: expected state of the Release event.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = monitor.get_monitor_id()
        if monitor_id is None:
            return False

        def _check_release():
            """Handler for the release event."""
            return self.get_event_count(app_id, monitor_id, 'Release') == 1

        released = False
        try:
            utils.poll_for_condition(
                    condition=_check_release,
                    timeout=self.ADD_MONITOR_POLLING_TIMEOUT_SECS,
                    sleep_interval=self.ADD_MONITOR_POLLING_SLEEP_SECS,
                    desc='Waiting for release')
            released = True
        except utils.TimeoutError as e:
            logging.error('release: %s', e)
        except Exception as e:
            logging.error('release: %s', e)
        except:
            logging.error('release: unexpected error')

        return expected == released


    @test_retry_and_log(False)
    def test_device_found(self, monitor, count, delay=0):
        """Test if the DeviceFound method on a monitor has been invoked or not.

        @param monitor: the local monitor object.
        @param count: expected count of the DeviceFound events.
        @param delay: wait until 'delay' seconds before reading the event count.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = monitor.get_monitor_id()
        if monitor_id is None:
            return False

        if delay:
            time.sleep(delay)

        checked_count = self.get_event_count(app_id, monitor_id, 'DeviceFound')

        if count == self.MULTIPLE_EVENTS:
            return checked_count > 1

        return checked_count == count


    @test_retry_and_log(False)
    def test_device_lost(self, monitor, count, delay=0):
        """Test if the DeviceLost method on a monitor has been invoked or not.

        @param monitor: the local monitor object.
        @param count: expected count of the DeviceLost events.
        @param delay: wait until 'delay' seconds before reading the event count.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = monitor.get_monitor_id()
        if monitor_id is None:
            return False

        if delay:
            time.sleep(delay)

        checked_count = self.get_event_count(app_id, monitor_id, 'DeviceLost')

        if count == self.MULTIPLE_EVENTS:
            return checked_count > 1

        return checked_count == count


    @test_retry_and_log(False)
    def test_reset_event_count(self, monitor, event='All'):
        """Test resetting count of a particular event on the given monitor.

        @param monitor: the local monitor object.
        @param event: name of the specific event or 'All' for all events.

        @returns: True on success, False otherwise.

        """
        return self.reset_event_count(monitor.get_app_id(),
                                      monitor.get_monitor_id(),
                                      event)


    @test_retry_and_log(False)
    def test_add_monitor(self, monitor, expected_activate=None,
                         expected_release=None):
        """Test adding a monitor.

        @param monitor: the local monitor object.
        @param expected_activate: expected state of the Activate event.
        @param expected_release: expected state of the Release event.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = self.add_monitor(app_id, monitor.get_monitor_data())
        if monitor_id is None:
            return False
        monitor.update_monitor_id(monitor_id)

        checked_activate = True
        if expected_activate is not None:
            checked_activate = self.test_monitor_activate(
                    monitor, expected_activate)

        checked_release = True
        if expected_release is not None:
            checked_release = self.test_monitor_release(
                    monitor, expected_release)

        if self.get_event_count(app_id, monitor_id, 'Release') != 0:
            self.remove_monitor(app_id, monitor_id)
            monitor.update_monitor_id(None)

        self.results = {
                'activated': checked_activate,
                'released': checked_release
        }
        return all(self.results.values())


    @test_retry_and_log(False)
    def test_remove_monitor(self, monitor):
        """Test removing a monitor.

        @param monitor: the local monitor object.

        @returns: True on success, False otherwise.

        """
        app_id = monitor.get_app_id()
        monitor_id = monitor.get_monitor_id()
        if monitor_id is None:
            return False

        ret = self.remove_monitor(app_id, monitor_id)
        monitor.update_monitor_id(None)

        if ret is None:
            return False

        return True


    @test_retry_and_log(False)
    def test_setup_peer_devices(self):
        """Test availability of the peer devices.

        @returns: True on success, False otherwise.

        """
        self.peer_keybd = None
        self.peer_mouse = None

        for device_type, device_list in self.devices.items():
            for device in device_list:
                if device_type is 'BLE_KEYBOARD':
                    self.peer_keybd = device
                elif device_type is 'BLE_MOUSE':
                    self.peer_mouse = device

        if self.peer_keybd is not None and self.peer_mouse is not None:
            self.test_stop_peer_device_adv(self.peer_keybd)
            self.test_stop_peer_device_adv(self.peer_mouse)

        self.results = {
                'keybd': self.peer_keybd is not None,
                'mouse': self.peer_mouse is not None
        }
        return all(self.results.values())


    @test_retry_and_log(False)
    def test_start_peer_device_adv(self, device, duration=0):
        """Test enabling the peer device advertisements.

        @param device: the device object.
        @param duration: the duration of the advertisement.

        @returns: True on success, False otherwise.

        """
        ret = self.test_device_set_discoverable(device, True)

        if duration:
            time.sleep(duration)

        return ret


    @test_retry_and_log(False)
    def test_stop_peer_device_adv(self, device, duration=0):
        """Test disabling the peer device advertisements.

        @param device: the device object.
        @param duration: the duration of the advertisement disable.

        @returns: True on success, False otherwise.

        """
        ret = self.test_device_set_discoverable(device, False)

        if duration:
            time.sleep(duration)

        return ret


    def advmon_test_monitor_creation(self):
        """Test case: MONITOR_CREATION

        Validate register/unregister app and create/remove monitor.

        """
        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_rssi([-40, 5, -60, 5])
        monitor1.update_patterns([
                [0, 0x19, [0xc2, 0x03]],
        ])

        monitor2 = TestMonitor(app1)
        monitor2.update_type('or_patterns')
        monitor2.update_rssi([-40, 10, -60, 10])
        monitor2.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])

        # Read supported types and features, should not fail.
        self.test_supported_types()
        self.test_supported_features()

        # Activate/Release should not get called.
        self.test_add_monitor(monitor1,
                              expected_activate=False,
                              expected_release=False)

        # Register the app, should not fail.
        self.test_register_app(app1)

        # Already registered app path, should fail with AlreadyExists.
        self.test_register_app(app1, expected=False)

        # Activate should get called for the monitor added before register app.
        self.test_monitor_activate(monitor1, expected=True)

        # Correct monitor parameters, activate should get called.
        self.test_add_monitor(monitor2, expected_activate=True)

        # Remove a monitor, should not fail.
        self.test_remove_monitor(monitor1)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Already unregistered app path, should fail with DoesNotExists.
        self.test_unregister_app(app1, expected=False)

        # Release should get called for a monitor not removed before unregister.
        self.test_monitor_release(monitor2, expected=True)

        # Remove another monitor, should not fail.
        self.test_remove_monitor(monitor2)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_monitor_validity(self):
        """Test case: MONITOR_VALIDITY

        Validate monitor parameters - monitor type, patterns, RSSI filter
        values.

        """
        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('incorrect_pattern')
        monitor1.update_rssi([-40, 5, -60, 5])
        monitor1.update_patterns([
                [0, 0x19, [0xc2, 0x03]],
        ])

        monitor2 = TestMonitor(app1)
        monitor2.update_type('or_patterns')
        monitor2.update_rssi([-40, 10, -60, 10])
        monitor2.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])

        # Register the app, should not fail.
        self.test_register_app(app1)

        # Incorrect monitor type, release should get called.
        self.test_add_monitor(monitor1, expected_release=True)

        # Incorrect rssi parameters, release should get called.
        monitor2.update_rssi([-40, 0, -60, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([-40, 10, -60, 0])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([40, 10, -60, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([-140, 10, -60, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([-40, 10, 60, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([-40, 10, -160, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_rssi([-60, 10, -40, 10])
        self.test_add_monitor(monitor2, expected_release=True)

        # Unset the rssi filter parameters.
        monitor2.update_rssi([127, 0, 127, 0])

        # Incorrect pattern parameters, release should get called.
        monitor2.update_patterns([
                [32, 0x09, 'MOUSE'],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_patterns([
                [0, 0x00, 'MOUSE'],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_patterns([
                [0, 0x40, 'MOUSE'],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_patterns([
                [0, 0x09, '0123456789ABCDEF0123456789ABCDEF0'],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_patterns([
                [32, 0x09, [0xc2, 0x03]],
                [0, 3, [0x12, 0x18]],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        monitor2.update_patterns([
                [0, 0x19, [0xc2, 0x03]],
                [0, 0x00, [0x12, 0x18]],
        ])
        self.test_add_monitor(monitor2, expected_release=True)

        # Correct pattern parameters, activate should get called.
        monitor2.update_patterns([
                [0, 0x09, 'MOUSE'],
        ])
        self.test_add_monitor(monitor2, expected_activate=True)
        self.test_remove_monitor(monitor2)

        monitor2.update_rssi([-40, 10, -60, 10])
        monitor2.update_patterns([
                [0, 0x19, [0xc2, 0x03]],
                [0, 0x03, [0x12, 0x18]],
        ])
        self.test_add_monitor(monitor2, expected_activate=True)
        self.test_remove_monitor(monitor2)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_pattern_filter_1(self):
        """Test case: PATTERN_FILTER_1

        Verify matching of advertisements w.r.t. various pattern values and
        different AD Data Types - Local Name Service UUID and Device Type.

        """
        self.test_setup_peer_devices()

        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_rssi([-60, 3, -80, 3])

        # Register the app, should not fail.
        self.test_register_app(app1)

        monitor1.update_patterns([
                [5, 0x09, '_REF'],
        ])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Local name 'KEYBD_REF' should match.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=1)

        # Local name 'MOUSE_REF' should match.
        self.test_start_peer_device_adv(self.peer_mouse, duration=5)
        self.test_device_found(monitor1, count=2)

        self.test_stop_peer_device_adv(self.peer_keybd)
        self.test_stop_peer_device_adv(self.peer_mouse)
        self.test_remove_monitor(monitor1)

        monitor1.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Service UUID 0x1812 should match.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=1)

        # Service UUID 0x1812 should match.
        self.test_start_peer_device_adv(self.peer_mouse, duration=5)
        self.test_device_found(monitor1, count=2)

        self.test_stop_peer_device_adv(self.peer_keybd)
        self.test_stop_peer_device_adv(self.peer_mouse)
        self.test_remove_monitor(monitor1)

        monitor1.update_patterns([
                [0, 0x19, [0xc1, 0x03]],
                [0, 0x09, 'MOUSE'],
        ])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Device type 0xc103 should match.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=1)

        # Local name 'MOUSE_REF' should match.
        self.test_start_peer_device_adv(self.peer_mouse, duration=5)
        self.test_device_found(monitor1, count=2)

        self.test_stop_peer_device_adv(self.peer_keybd)
        self.test_stop_peer_device_adv(self.peer_mouse)
        self.test_remove_monitor(monitor1)

        monitor1.update_patterns([
                [0, 0x19, [0xc1, 0x03]],
                [0, 0x19, [0xc3, 0x03]],
        ])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Device type 0xc103 should match.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=1)

        # Device type 0xc203 should not match.
        self.test_start_peer_device_adv(self.peer_mouse, duration=5)
        self.test_device_found(monitor1, count=1)

        self.test_stop_peer_device_adv(self.peer_keybd)
        self.test_stop_peer_device_adv(self.peer_mouse)
        self.test_remove_monitor(monitor1)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_rssi_filter_1(self):
        """Test case: RSSI_FILTER_1

        Verify unset RSSI filter and filter with no matching RSSI values.

        """
        self.test_setup_peer_devices()

        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])

        # Register the app, should not fail.
        self.test_register_app(app1)

        monitor1.update_rssi([127, 0, 127, 0])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Unset RSSI filter, adv should match multiple times.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=self.MULTIPLE_EVENTS)

        # Unset RSSI filter, DeviceLost should not get triggered.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_lost(monitor1, count=0)

        self.test_remove_monitor(monitor1)

        monitor1.update_rssi([-10, 5, -20, 5])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Adv RSSI lower than RSSI filter, DeviceFound should not get triggered.
        self.test_start_peer_device_adv(self.peer_keybd, duration=10)
        self.test_device_found(monitor1, count=0)

        # No device was found earlier, so DeviceLost should not get triggered.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=10)
        self.test_device_lost(monitor1, count=0)

        self.test_remove_monitor(monitor1)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_rssi_filter_2(self):
        """Test case: RSSI_FILTER_2

        Verify RSSI filter matching with multiple peer devices.

        """
        self.test_setup_peer_devices()

        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])

        # Register the app, should not fail.
        self.test_register_app(app1)

        monitor1.update_rssi([-60, 3, -80, 3])
        self.test_add_monitor(monitor1, expected_activate=True)

        # DeviceFound should get triggered only once per device.
        self.test_start_peer_device_adv(self.peer_keybd, duration=10)
        self.test_device_found(monitor1, count=1)

        # DeviceFound should get triggered for another device.
        self.test_start_peer_device_adv(self.peer_mouse, duration=10)
        self.test_device_found(monitor1, count=2)

        # DeviceLost should get triggered only once per device.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=10)
        self.test_device_lost(monitor1, count=1)

        # DeviceLost should get triggered for another device.
        self.test_stop_peer_device_adv(self.peer_mouse, duration=10)
        self.test_device_lost(monitor1, count=2)

        self.test_remove_monitor(monitor1)

        monitor1.update_rssi([-60, 10, -80, 10])
        self.test_add_monitor(monitor1, expected_activate=True)

        # Device was online for short period of time, so DeviceFound should
        # not get triggered.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=0)

        # Device did not come back online, DeviceFound should not get triggered.
        # No device was found earlier, so DeviceLost should not get triggered.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=15)
        self.test_device_found(monitor1, count=0)
        self.test_device_lost(monitor1, count=0)

        self.test_remove_monitor(monitor1)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_rssi_filter_3(self):
        """Test case: RSSI_FILTER_3

        Verify reset of RSSI timers based on advertisements.

        """
        self.test_setup_peer_devices()

        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])

        # Register the app, should not fail.
        self.test_register_app(app1)

        monitor1.update_rssi([-60, 10, -80, 10])
        self.test_add_monitor(monitor1, expected_activate=True)

        # DeviceFound should not get triggered before timeout.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=0)

        # DeviceFound should not get triggered as device went offline.
        # No device was found earlier, so DeviceLost should not get triggered.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=10)
        self.test_device_found(monitor1, count=0)
        self.test_device_lost(monitor1, count=0)

        # Timer should get reset, so DeviceFound should not get triggered.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=0)

        # DeviceFound should get triggered once timer completes.
        self.test_device_found(monitor1, count=1, delay=10)

        # DeviceLost should not get triggered before timeout.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_lost(monitor1, count=0)

        # Timer should get reset, so DeviceLost should not get triggered.
        # DeviceFound should not get triggered as device is not lost yet.
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_lost(monitor1, count=0)
        self.test_device_found(monitor1, count=1)

        # Timer should get reset, so DeviceLost should not get triggered.
        self.test_stop_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_lost(monitor1, count=0)

        # DeviceLost should get triggered once timer completes.
        self.test_device_lost(monitor1, count=1, delay=10)

        self.test_remove_monitor(monitor1)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)


    def advmon_test_fg_bg_combination(self):
        """Test case: FG_BG_COMBINATION

        Verify background scanning and foreground scanning do not interfere
        working of each other.

        """
        self.test_setup_peer_devices()

        # Create a test app instance.
        app1 = self.create_app()

        monitor1 = TestMonitor(app1)
        monitor1.update_type('or_patterns')
        monitor1.update_patterns([
                [0, 0x03, [0x12, 0x18]],
        ])
        monitor1.update_rssi([127, 0, 127, 0])

        # Register the app, should not fail.
        self.test_register_app(app1)

        # Activate should get invoked.
        self.test_add_monitor(monitor1, expected_activate=True)

        # Pair/connect LE Mouse.
        self.test_start_peer_device_adv(self.peer_mouse, duration=5)
        time.sleep(self.PAIR_TEST_SLEEP_SECS)
        self.test_discover_device(self.peer_mouse.address)
        time.sleep(self.PAIR_TEST_SLEEP_SECS)
        self.test_pairing(self.peer_mouse.address, self.peer_mouse.pin)
        time.sleep(self.PAIR_TEST_SLEEP_SECS)
        self.test_connection_by_adapter(self.peer_mouse.address)
        self.test_connection_by_device(self.peer_mouse)

        # DeviceFound should get triggered for keyboard.
        self.test_reset_event_count(monitor1)
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=self.MULTIPLE_EVENTS)
        self.test_stop_peer_device_adv(self.peer_keybd)

        # Start foreground scanning.
        self.test_start_discovery()

        # Disconnect LE mouse.
        self.test_disconnection_by_device(self.peer_mouse)

        # Remove the monitor.
        self.test_remove_monitor(monitor1)

        # Activate should get invoked.
        self.test_add_monitor(monitor1, expected_activate=True)

        # Connect LE mouse.
        self.test_connection_by_device(self.peer_mouse)

        # DeviceFound should get triggered for keyboard.
        self.test_reset_event_count(monitor1)
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=self.MULTIPLE_EVENTS)
        self.test_stop_peer_device_adv(self.peer_keybd)

        # Stop foreground scanning.
        self.test_stop_discovery()

        # Disconnect LE mouse.
        self.test_disconnection_by_device(self.peer_mouse)

        # DeviceFound should get triggered for keyboard.
        self.test_reset_event_count(monitor1)
        self.test_start_peer_device_adv(self.peer_keybd, duration=5)
        self.test_device_found(monitor1, count=self.MULTIPLE_EVENTS)
        self.test_stop_peer_device_adv(self.peer_keybd)

        # Remove the monitor.
        self.test_remove_monitor(monitor1)

        # Connect LE mouse.
        self.test_connection_by_device(self.peer_mouse)

        # Unregister the app, should not fail.
        self.test_unregister_app(app1)

        # Terminate the test app instance.
        self.test_exit_app(app1)

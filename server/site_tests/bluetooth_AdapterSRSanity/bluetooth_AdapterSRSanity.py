# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
""" Server-side bluetooth adapter tests that involve suspend/resume with peers

paired and/or connected.

Single btpeer tests:
  - Reconnect on resume test
    - Classic HID
    - LE HID
    - A2DP
  - Wake from suspend test
    - Classic HID
    - LE HID
    - A2DP shouldn't wake from suspend
  - Suspend while discovering (discovering should pause and unpause)
  - Suspend while advertising (advertising should pause and unpause)

Multiple btpeer tests:
  - Reconnect on resume test
    - One classic HID, One LE HID
    - Two classic HID
    - Two LE HID
  - Wake from suspend test
    - Two classic HID
    - Two classic LE
"""

from autotest_lib.server.cros.bluetooth.bluetooth_adapter_tests import \
     BluetoothAdapterTests, TABLET_MODELS, REALTEK_MODELS
from autotest_lib.server.cros.bluetooth.bluetooth_adapter_quick_tests import \
     BluetoothAdapterQuickTests

test_wrapper = BluetoothAdapterQuickTests.quick_test_test_decorator
batch_wrapper = BluetoothAdapterQuickTests.quick_test_batch_decorator

SUSPEND_SEC = 15
EXPECT_NO_WAKE_SUSPEND_SEC = 30
EXPECT_PEER_WAKE_SUSPEND_SEC = 60


class bluetooth_AdapterSRSanity(BluetoothAdapterQuickTests,
                                BluetoothAdapterTests):
    """Server side bluetooth adapter suspend resume test with peer."""


    def test_discover_and_pair(self, device):
        """ Discovers and pairs given device. Automatically connects too."""
        self.test_device_set_discoverable(device, True)
        self.test_discover_device(device.address)
        self.test_pairing(device.address, device.pin, trusted=True)

    def _test_keyboard_with_string(self, device):
        self.test_keyboard_input_from_trace(device, "simple_text")

    # ---------------------------------------------------------------
    # Reconnect after suspend tests
    # ---------------------------------------------------------------

    def run_reconnect_device(self, devtuples):
        """ Reconnects a device after suspend/resume.

        @param devtuples: array of tuples consisting of the following
                            * device_type: MOUSE, BLE_MOUSE, etc.
                            * device: meta object for peer device
                            * device_test: Optional; test function to run w/
                                           device (for example, mouse click)
        """
        boot_id = self.host.get_boot_id()
        suspend = self.suspend_async(suspend_time=SUSPEND_SEC)

        try:
            for _, device, device_test in devtuples:
                self.test_discover_and_pair(device)
                self.test_device_set_discoverable(device, False)
                self.test_connection_by_adapter(device.address)

            # Trigger suspend, wait for regular resume, verify we can reconnect
            # and run device specific test
            self.test_suspend_and_wait_for_sleep(
                suspend, sleep_timeout=SUSPEND_SEC)
            self.test_wait_for_resume(
                boot_id, suspend, resume_timeout=SUSPEND_SEC)

            for device_type, device, device_test in devtuples:
                if 'BLE' in device_type:
                    # LE can't reconnect without advertising/discoverable
                    self.test_device_set_discoverable(device, True)
                    # Make sure we're actually connected
                    self.test_device_is_connected(device.address)
                else:
                    # Classic requires peer to initiate a connection to wake up
                    # the dut
                    self.test_connection_by_device(device)

                # Make sure hid device was created before using it
                self.test_hid_device_created(device.address)
                if device_test is not None:
                    device_test(device)

        finally:
            for _, device, __ in devtuples:
                self.test_remove_pairing(device.address)

    @test_wrapper('Reconnect Classic HID', devices={'MOUSE': 1})
    def sr_reconnect_classic_hid(self):
        """ Reconnects a classic HID device after suspend/resume. """
        device_type = 'MOUSE'
        device = self.devices[device_type][0]
        self.run_reconnect_device([(device_type, device,
                                    self.test_mouse_left_click)])

    @test_wrapper('Reconnect LE HID', devices={'BLE_MOUSE': 1})
    def sr_reconnect_le_hid(self):
        """ Reconnects a LE HID device after suspend/resume. """
        device_type = 'BLE_MOUSE'
        device = self.devices[device_type][0]
        self.run_reconnect_device([(device_type, device,
                                    self.test_mouse_left_click)])

    @test_wrapper('Reconnect A2DP', devices={})
    def sr_reconnect_a2dp(self):
        """ Reconnects an A2DP device after suspend/resume. """
        raise NotImplementedError()

    @test_wrapper('Reconnect Multiple Classic HID',
                   devices={'MOUSE': 1, 'KEYBOARD': 1})
    def sr_reconnect_multiple_classic_hid(self):
        """ Reconnects multiple classic HID devices after suspend/resume. """
        devices = [
                ('MOUSE', self.devices['MOUSE'][0], self.test_mouse_left_click),
                ('KEYBOARD', self.devices['KEYBOARD'][0],
                    self._test_keyboard_with_string)
        ]
        self.run_reconnect_device(devices)

    @test_wrapper('Reconnect Multiple LE HID',
                  devices={'BLE_MOUSE': 1, 'BLE_KEYBOARD': 1})
    def sr_reconnect_multiple_le_hid(self):
        """ Reconnects multiple LE HID devices after suspend/resume. """
        devices = [
                ('BLE_MOUSE', self.devices['BLE_MOUSE'][0],
                 self.test_mouse_left_click),
                ('BLE_KEYBOARD', self.devices['BLE_KEYBOARD'][0],
                 self._test_keyboard_with_string)
        ]
        self.run_reconnect_device(devices)

    @test_wrapper(
        'Reconnect one of each classic+LE HID',
        devices={
            'BLE_MOUSE': 1,
            'KEYBOARD': 1
        })
    def sr_reconnect_multiple_classic_le_hid(self):
        """ Reconnects one of each classic and LE HID devices after
            suspend/resume.
        """
        devices = [
                ('BLE_MOUSE', self.devices['BLE_MOUSE'][0],
                 self.test_mouse_left_click),
                ('KEYBOARD', self.devices['KEYBOARD'][0],
                 self._test_keyboard_with_string)
        ]
        self.run_reconnect_device(devices)

    # ---------------------------------------------------------------
    # Wake from suspend tests
    # ---------------------------------------------------------------

    def run_peer_wakeup_device(self, device_type, device, device_test=None):
        """ Uses paired peer device to wake the device from suspend.

        @param device_type: the device type (used to determine if it's LE)
        @param device: the meta device with the paired device
        @param device_test: What to test to run after waking and connecting the
                            adapter/host
        """
        boot_id = self.host.get_boot_id()
        suspend = self.suspend_async(
            suspend_time=EXPECT_PEER_WAKE_SUSPEND_SEC, expect_bt_wake=True)

        # Clear wake before testing
        self.test_adapter_set_wake_disabled()

        try:
            self.test_discover_and_pair(device)
            self.test_device_set_discoverable(device, False)

            adapter_address = self.bluetooth_facade.address

            # Confirm connection completed
            self.test_device_is_connected(device.address)

            # Wait until powerd marks adapter as wake enabled
            self.test_adapter_wake_enabled()

            # Trigger suspend, asynchronously trigger wake and wait for resume
            self.test_suspend_and_wait_for_sleep(
                suspend, sleep_timeout=5)

            # Trigger peer wakeup
            peer_wake = self.device_connect_async(device_type,
                                                  device,
                                                  adapter_address,
                                                  delay_wake=5)
            peer_wake.start()

            # Expect a quick resume. If a timeout occurs, test fails. Since we
            # delay sending the wake signal, we should accomodate that in our
            # expected timeout.
            self.test_wait_for_resume(
                boot_id, suspend, resume_timeout=SUSPEND_SEC + 5,
                fail_on_timeout=True)

            # Finish peer wake process
            peer_wake.join()

            # Make sure we're actually connected
            self.test_device_is_connected(device.address)
            self.test_hid_device_created(device.address)

            if device_test is not None:
                device_test(device)

        finally:
            self.test_remove_pairing(device.address)


    # TODO(b/151332866) - Bob can't wake from suspend due to wrong power/wakeup
    # TODO(b/150897528) - Dru is powered down during suspend, won't wake up
    @test_wrapper('Peer wakeup Classic HID',
                  devices={'MOUSE': 1},
                  skip_models=TABLET_MODELS + REALTEK_MODELS + ['bob', 'dru'])
    def sr_peer_wake_classic_hid(self):
        """ Use classic HID device to wake from suspend. """
        device = self.devices['MOUSE'][0]
        self.run_peer_wakeup_device(
            'MOUSE', device, device_test=self.test_mouse_left_click)

    # TODO(b/151332866) - Bob can't wake from suspend due to wrong power/wakeup
    # TODO(b/150897528) - Dru is powered down during suspend, won't wake up
    @test_wrapper('Peer wakeup LE HID', devices={'BLE_MOUSE': 1},
                  skip_models=TABLET_MODELS + REALTEK_MODELS + ['bob', 'dru'],
                  )
    def sr_peer_wake_le_hid(self):
        """ Use LE HID device to wake from suspend. """
        device = self.devices['BLE_MOUSE'][0]
        self.run_peer_wakeup_device(
            'BLE_MOUSE', device, device_test=self.test_mouse_left_click)

    @test_wrapper('Peer wakeup with A2DP should fail')
    def sr_peer_wake_a2dp_should_fail(self):
        """ Use A2DP device to wake from suspend and fail. """
        raise NotImplementedError()

    # ---------------------------------------------------------------
    # Suspend while discovering and advertising
    # ---------------------------------------------------------------

    # TODO(b/150897528) - Scarlet Dru loses firmware around suspend
    @test_wrapper('Suspend while discovering', devices={'BLE_MOUSE': 1},
                  skip_models=REALTEK_MODELS + ['dru'])
    def sr_while_discovering(self):
        """ Suspend while discovering. """
        device = self.devices['BLE_MOUSE'][0]
        boot_id = self.host.get_boot_id()
        suspend = self.suspend_async(suspend_time=EXPECT_NO_WAKE_SUSPEND_SEC)

        # We don't pair to the peer device because we don't want it in the
        # allowlist. However, we want an advertising peer in this test
        # responding to the discovery requests.
        self.test_device_set_discoverable(device, True)

        self.test_start_discovery()
        self.test_suspend_and_wait_for_sleep(
            suspend, sleep_timeout=EXPECT_NO_WAKE_SUSPEND_SEC)

        # If discovery events wake us early, we will raise and suspend.exitcode
        # will be non-zero
        self.test_wait_for_resume(
            boot_id, suspend, resume_timeout=EXPECT_NO_WAKE_SUSPEND_SEC)

        # Discovering should restore after suspend
        self.test_is_discovering()

        self.test_stop_discovery()

    # TODO(b/150897528) - Scarlet Dru loses firmware around suspend
    @test_wrapper('Suspend while advertising', devices={'MOUSE': 1},
                  skip_models=REALTEK_MODELS + ['dru'])
    def sr_while_advertising(self):
        """ Suspend while advertising. """
        device = self.devices['MOUSE'][0]
        boot_id = self.host.get_boot_id()
        suspend = self.suspend_async(suspend_time=EXPECT_NO_WAKE_SUSPEND_SEC)

        self.test_discoverable()
        self.test_suspend_and_wait_for_sleep(
            suspend, sleep_timeout=SUSPEND_SEC)

        # Peer device should not be able to discover us in suspend
        self.test_discover_by_device_fails(device)

        self.test_wait_for_resume(boot_id,
                                  suspend,
                                  resume_timeout=EXPECT_NO_WAKE_SUSPEND_SEC)

        # Test that we are properly discoverable again
        self.test_is_discoverable()
        self.test_discover_by_device(device)

        self.test_nondiscoverable()

    # ---------------------------------------------------------------
    # Sanity checks
    # ---------------------------------------------------------------

    @test_wrapper('Suspend while powered off', devices={'MOUSE': 1})
    def sr_while_powered_off(self):
        """ Suspend while adapter is powered off. """
        device = self.devices['MOUSE'][0]
        boot_id = self.host.get_boot_id()
        suspend = self.suspend_async(suspend_time=SUSPEND_SEC)

        # Pair device so we have something to do in suspend
        self.test_discover_and_pair(device)

        # Trigger power down and quickly suspend
        self.test_power_off_adapter()
        self.test_suspend_and_wait_for_sleep(
            suspend, sleep_timeout=SUSPEND_SEC)
        # Suspend and resume should succeed
        self.test_wait_for_resume(
            boot_id, suspend, resume_timeout=SUSPEND_SEC)

        # We should be able to power it back on
        self.test_power_on_adapter()

        # Test that we can reconnect to the device after powering back on
        self.test_connection_by_device(device)

    @batch_wrapper('SR with Peer Sanity')
    def sr_sanity_batch_run(self, num_iterations=1, test_name=None):
        """ Batch of suspend/resume peer sanity tests. """
        self.sr_reconnect_classic_hid()
        self.sr_reconnect_le_hid()
        self.sr_peer_wake_classic_hid()
        self.sr_peer_wake_le_hid()
        self.sr_while_discovering()
        self.sr_while_advertising()
        self.sr_reconnect_multiple_classic_hid()
        self.sr_reconnect_multiple_le_hid()
        self.sr_reconnect_multiple_classic_le_hid()

    def run_once(self,
                 host,
                 num_iterations=1,
                 test_name=None,
                 flag='Quick Sanity'):
        """Running Bluetooth adapter suspend resume with peer autotest.

        @param host: the DUT, usually a chromebook
        @param num_iterations: the number of times to execute the test
        @param test_name: the test to run or None for all tests
        @param flag: run tests with this flag (default: Quick Sanity)

        """

        # Initialize and run the test batch or the requested specific test
        self.quick_test_init(host, use_btpeer=True, flag=flag)
        self.sr_sanity_batch_run(num_iterations, test_name)
        self.quick_test_cleanup()

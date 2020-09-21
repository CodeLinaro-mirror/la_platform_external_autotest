# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A Batch of Bluetooth AUdio Sanity tests"""

from autotest_lib.client.common_lib import error
from autotest_lib.client.cros.bluetooth.bluetooth_audio_test_data import (
        A2DP, AVRCP, HFP_WBS, HFP_NBS)
from autotest_lib.server.cros.bluetooth.bluetooth_adapter_quick_tests import (
        BluetoothAdapterQuickTests)
from autotest_lib.server.cros.bluetooth.bluetooth_adapter_audio_tests import (
        BluetoothAdapterAudioTests)


class bluetooth_AdapterAUSanity(BluetoothAdapterQuickTests,
                                BluetoothAdapterAudioTests):
    """A Batch of Bluetooth audio sanity tests."""

    test_wrapper = BluetoothAdapterQuickTests.quick_test_test_decorator
    batch_wrapper = BluetoothAdapterQuickTests.quick_test_batch_decorator


    def au_run_method(self, device, test_method, test_profile):
        """audio procedure of running a specified test method.

        @param device: the bt peer device
        @param test_method: the audio test method to run
        @param test_profile: which test profile is used,
                             A2DP, HFP_WBS or HFP_NBS
        """
        self.test_reset_on_adapter()
        self.test_bluetoothd_running()
        self.initialize_bluetooth_audio(device, test_profile)
        self.test_device_set_discoverable(device, True)
        self.test_discover_device(device.address)
        self.test_pairing(device.address, device.pin, trusted=True)
        self.test_connection_by_adapter(device.address)
        test_method()
        self.test_disconnection_by_adapter(device.address)
        self.cleanup_bluetooth_audio(device, test_profile)


    @test_wrapper('A2DP sinewave test', devices={'BLUETOOTH_AUDIO':1})
    def au_a2dp_test(self):
        """A2DP test with sinewaves on the two channels."""
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_run_method(
                device, lambda: self.test_a2dp_sinewaves(device), A2DP)


    def check_wbs_capability(self):
        """Check if the DUT supports WBS capability.

        @raises: TestNAError if the dut does not support wbs.
        """
        capabilities, err = self.bluetooth_facade.get_supported_capabilities()
        if not (err is None and bool(capabilities.get('wide band speech'))):
            raise error.TestNAError(
                    'The DUT does not support WBS. Skip the test.')


    def au_hfp_run_method(self, device, test_method, test_profile):
        """HFP WBS test with the specified test method.

        @param device: the bt peer device
        @param test_method: the specific HFP WBS test method
        @param test_profile: which test profile is used, HFP_WBS or HFP_NBS
        """
        # Enable/disable WBS per test_profile.
        wbs_flag = test_profile == HFP_WBS
        if not self.bluetooth_facade.enable_wbs(wbs_flag):
            raise error.TestError('failed to %s wbs',
                                  'enable' if wbs_flag else 'disable')

        self.au_run_method(
                device, lambda: test_method(device, test_profile), test_profile)


    @test_wrapper('HFP WBS sinewave test with dut as source',
                  devices={'BLUETOOTH_AUDIO':1})
    def au_hfp_wbs_dut_as_source_test(self):
        """HFP WBS test with sinewave streaming from dut to peer."""
        self.check_wbs_capability()
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_hfp_run_method(device, self.test_hfp_dut_as_source, HFP_WBS)


    @test_wrapper('HFP WBS sinewave test with dut as sink',
                  devices={'BLUETOOTH_AUDIO':1})
    def au_hfp_wbs_dut_as_sink_test(self):
        """HFP WBS test with sinewave streaming from peer to dut."""
        self.check_wbs_capability()
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_hfp_run_method(device, self.test_hfp_dut_as_sink, HFP_WBS)


    @test_wrapper('HFP NBS sinewave test with dut as source',
                  devices={'BLUETOOTH_AUDIO':1})
    def au_hfp_nbs_dut_as_source_test(self):
        """HFP NBS test with sinewave streaming from dut to peer."""
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_hfp_run_method(device, self.test_hfp_dut_as_source, HFP_NBS)


    @test_wrapper('HFP NBS sinewave test with dut as sink',
                  devices={'BLUETOOTH_AUDIO':1})
    def au_hfp_nbs_dut_as_sink_test(self):
        """HFP NBS test with sinewave streaming from peer to dut."""
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_hfp_run_method(device, self.test_hfp_dut_as_sink, HFP_NBS)



    def au_run_avrcp_method(self, device, test_method):
        """avrcp procedure of running a specified test method.

        @param device: the bt peer device
        @param test_method: the avrcp test method to run
        """
        def wrapped_test_method(device):
            """A wrapper method to initialize and cleanup avrcp tests.

            @param device: the bt peer device
            """
            self.initialize_bluetooth_player(device)
            test_method(device)
            self.cleanup_bluetooth_player(device)

        self.au_run_method(
                device, lambda: wrapped_test_method(device), AVRCP)


    @test_wrapper('avrcp command test', devices={'BLUETOOTH_AUDIO':1})
    def au_avrcp_command_test(self):
        """AVRCP test to examine commands reception."""
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_run_avrcp_method(device, self.test_avrcp_commands)


    @test_wrapper('avrcp media info test', devices={'BLUETOOTH_AUDIO':1})
    def au_avrcp_media_info_test(self):
        """AVRCP test to examine metadata propgation."""
        device = self.devices['BLUETOOTH_AUDIO'][0]
        self.au_run_avrcp_method(device, self.test_avrcp_media_info)


    @batch_wrapper('Bluetooth Audio Batch Sanity Tests')
    def au_sanity_batch_run(self, num_iterations=1, test_name=None):
        """Run the bluetooth audio sanity test batch or a specific given test.

        @param num_iterations: how many iterations to run
        @param test_name: specific test to run otherwise None to run the
                whole batch
        """
        self.au_a2dp_test()
        self.au_hfp_nbs_dut_as_source_test()
        self.au_hfp_nbs_dut_as_sink_test()
        self.au_hfp_wbs_dut_as_source_test()
        self.au_hfp_wbs_dut_as_sink_test()
        self.au_avrcp_command_test()
        self.au_avrcp_media_info_test()


    def run_once(self, host, num_iterations=1, test_name=None,
                 flag='Quick Sanity'):
        """Run the batch of Bluetooth stand sanity tests

        @param host: the DUT, usually a chromebook
        @param num_iterations: the number of rounds to execute the test
        @param test_name: the test to run, or None for all tests
        """
        self.host = host

        self.quick_test_init(host, use_btpeer=True, flag=flag)
        self.au_sanity_batch_run(num_iterations, test_name)
        self.quick_test_cleanup()

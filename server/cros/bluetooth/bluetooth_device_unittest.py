#!/usr/bin/python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for BluetoothDevice.get_hci."""

import unittest
from unittest import mock

import common  # isort: split

from autotest_lib.server.cros.bluetooth import bluetooth_device


class BluetoothDeviceGetHciTests(unittest.TestCase):
    """Tests BluetoothDevice.get_hci handling of missing HCI adapters."""

    def _make_device(self) -> bluetooth_device.BluetoothDevice:
        """Creates an uninitialized BluetoothDevice instance for unit tests."""
        return bluetooth_device.BluetoothDevice.__new__(
                bluetooth_device.BluetoothDevice)

    def test_get_hci_returns_adapter_name(self) -> None:
        """Verifies get_hci returns the adapter name when dev_info succeeds."""
        dev = self._make_device()
        with mock.patch.object(dev,
                               "get_dev_info",
                               return_value=[0, "hci0", "00:11:22:33:44:55"]):
            self.assertEqual(dev.get_hci(), "hci0")

    def test_get_hci_returns_none_when_no_active_hci(self) -> None:
        """Verifies get_hci returns None on 'No active hci' RPC failure."""
        dev = self._make_device()
        rpc_err = Exception(
                "Exception from client (RPC: floss.get_dev_info)\n"
                "autotest_lib.client.common_lib.error.TestError: No active hci"
        )
        with mock.patch.object(dev, "get_dev_info", side_effect=rpc_err):
            self.assertIsNone(dev.get_hci())

    def test_get_hci_reraises_unexpected_rpc_error(self) -> None:
        """Verifies get_hci propagates unrelated RPC errors."""
        dev = self._make_device()
        with mock.patch.object(dev,
                               "get_dev_info",
                               side_effect=RuntimeError("SSH disconnected")):
            with self.assertRaises(RuntimeError):
                dev.get_hci()


if __name__ == "__main__":
    unittest.main()

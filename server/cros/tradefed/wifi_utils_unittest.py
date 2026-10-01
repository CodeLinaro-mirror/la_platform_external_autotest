# Lint as: python2, python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import unittest
from unittest import mock

import common

from autotest_lib.server.cros.tradefed import wifi_utils


class _FakeHostInfo(object):
    """Stands in for server.hosts.host_info.HostInfo.

    Only get_label_value() is needed here, and reimplementing it keeps this
    test from importing server.hosts, which pulls in the whole host stack.
    """

    def __init__(self, labels):
        self.labels = labels

    def get_label_value(self, prefix):
        for label in self.labels:
            if label.startswith(prefix + ':'):
                return label[len(prefix) + 1:]
        return ''


def _machine(*labels):
    """Builds a machine dict whose store carries the given labels."""
    store = mock.Mock()
    store.get.return_value = _FakeHostInfo(list(labels))
    return {'hostname': 'some-host', 'host_info_store': store}


class GetWifiSsidPassTest(unittest.TestCase):
    """Unittest for wifi_utils.get_wifi_ssid_pass."""

    def test_reads_credentials_from_host_info(self):
        """CFT's wifisecret labels are preferred over everything else."""
        machine = _machine('board:brya', 'wifisecret_ssid:LAB-PDEIO',
                           'wifisecret_security:wpa',
                           'wifisecret_password:hunter2')

        self.assertEqual(wifi_utils.get_wifi_ssid_pass(machine),
                         ('LAB-PDEIO', 'hunter2'))

    def test_reads_ssid_of_open_network_from_host_info(self):
        """An SSID with no password is still a usable answer."""
        machine = _machine('wifisecret_ssid:LAB-GUEST')

        self.assertEqual(wifi_utils.get_wifi_ssid_pass(machine),
                         ('LAB-GUEST', ''))

    @mock.patch.object(wifi_utils.global_config.global_config,
                       'get_config_value',
                       return_value=None)
    @mock.patch.object(wifi_utils.utils, 'run')
    @mock.patch.object(wifi_utils.utils, 'is_in_container', return_value=True)
    @mock.patch.object(wifi_utils.utils, 'get_wireless_ssid', return_value='')
    def test_falls_back_when_labels_are_absent(self, get_wireless_ssid,
                                               is_in_container, run,
                                               get_config_value):
        """Without the labels we keep the pre-CFT behaviour.

        This is the path that produces the hardcoded HACK(b/309894984) SSID.
        """
        run.return_value = mock.Mock(stdout='gs-password\n')
        machine = _machine('board:brya')

        with self.assertLogs(level='DEBUG') as logs:
            result = wifi_utils.get_wifi_ssid_pass(machine)

        self.assertEqual(result, ('ChromeOS_lab_AP', 'gs-password'))
        self.assertTrue(any('falling back' in l for l in logs.output))
        self.assertFalse(any('gs-password' in l for l in logs.output))

    @mock.patch.object(wifi_utils.utils, 'is_in_container', return_value=False)
    @mock.patch.object(wifi_utils.utils, 'get_wireless_ssid', return_value='')
    def test_survives_an_unreadable_store(self, get_wireless_ssid,
                                          is_in_container):
        """A store that cannot be read must not fail the test run."""
        store = mock.Mock()
        store.get.side_effect = Exception('no backing file')
        machine = {'hostname': 'some-host', 'host_info_store': store}

        ssid, _ = wifi_utils.get_wifi_ssid_pass(machine)

        self.assertEqual(ssid, '')


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for ChameleonConnection and ChameleonHost XMLRPC handling."""

import http.client
import socket
import unittest
from unittest import mock

from autotest_lib.client.cros.chameleon import chameleon
from autotest_lib.server.hosts import chameleon_host
import common


class ChameleonConnectionTests(unittest.TestCase):
    """Tests timeout and reconnect behavior in ChameleonConnection."""

    def test_create_server_proxy_sets_socket_timeout(self) -> None:
        """Verifies _create_server_proxy attaches a bounded HTTP timeout."""
        conn = chameleon.ChameleonConnection("127.0.0.1", 9992)
        with mock.patch.object(chameleon._TimeoutXMLRPCTransport,
                               "request",
                               return_value=([], )):
            proxy = conn._create_server_proxy()
        http_conn = proxy("transport").make_connection("127.0.0.1:9992")
        self.assertEqual(http_conn.timeout,
                         chameleon.CHAMELEON_XMLRPC_TIMEOUT_SECONDS)

    def test_restart_chameleond_does_not_reinvoke_after_reconnect(
            self) -> None:
        """Verifies restart_chameleond reconnects once without a second call."""
        first_proxy = mock.MagicMock()
        first_proxy.restart_chameleond.side_effect = (
                http.client.RemoteDisconnected("closed"))
        second_proxy = mock.MagicMock()
        proxies = [first_proxy, second_proxy]

        conn = chameleon.ChameleonConnection(
                None, proxy_generator=lambda: proxies.pop(0))
        conn.restart_chameleond()

        first_proxy.restart_chameleond.assert_called_once_with()
        second_proxy.restart_chameleond.assert_not_called()

    def test_normal_rpc_retries_once_after_socket_error(self) -> None:
        """Verifies ordinary RPCs reconnect and retry once on socket.error."""
        first_proxy = mock.MagicMock()
        first_proxy.is_ready.side_effect = socket.timeout("timed out")
        second_proxy = mock.MagicMock()
        second_proxy.is_ready.return_value = True
        proxies = [first_proxy, second_proxy]

        conn = chameleon.ChameleonConnection(
                None, proxy_generator=lambda: proxies.pop(0))
        self.assertTrue(conn.is_ready())
        first_proxy.is_ready.assert_called_once_with()
        second_proxy.is_ready.assert_called_once_with()

    def test_chameleon_host_passes_request_timeout(self) -> None:
        """Verifies ChameleonHost configures request_timeout_seconds."""
        host = chameleon_host.ChameleonHost.__new__(
                chameleon_host.ChameleonHost)
        host.hostname = "127.0.0.1"
        tracker = mock.MagicMock()
        with (
                mock.patch.object(
                        chameleon_host.ssh_host.SSHHost,
                        "_initialize",
                        return_value=None,
                ),
                mock.patch.object(
                        chameleon_host.ChameleonHost,
                        "rpc_server_tracker",
                        new_callable=mock.PropertyMock,
                        return_value=tracker,
                ),
        ):
            host._initialize(chameleon_host="127.0.0.1", chameleon_port=9992)
            host._chameleon_connection._reconnect()

        tracker.xmlrpc_connect.assert_called_once_with(
                None,
                9992,
                ready_test_name=chameleon.CHAMELEON_READY_TEST,
                timeout_seconds=60,
                request_timeout_seconds=(
                        chameleon.CHAMELEON_XMLRPC_TIMEOUT_SECONDS),
        )


if __name__ == "__main__":
    unittest.main()

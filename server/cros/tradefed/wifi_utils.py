# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import logging

from autotest_lib.client.common_lib import global_config, utils

_GS_WIFI_PASSWORD_PATH = 'gs://chromeos-arc-images/cts/wifi-password.txt'

# Host info labels carrying the credentials of the DUT's wifi network.
#
# cros-test derives them from the wifi_secret of the DUT topology and passes
# them to autoserv in --host_labels. See AppendChromeOsLabels() in
# infra/go/src/infra/cros/cmd/cft/execution/cros-test/internal/device/dut_info.go
_SSID_LABEL = 'wifisecret_ssid'
_PASSWORD_LABEL = 'wifisecret_password'


def _get_wifi_from_host_info(machine):
    """Retrieves the Wifi credentials CFT put in the host info store.

    Reads machine['host_info_store'] rather than calling
    host_info.get_store_from_machine(). Importing that module executes
    server/hosts/__init__.py, which drags the entire host stack into what is
    otherwise a leaf helper.

    Args:
        machine: A machine dict.

    Returns:
        A tuple (ssid: str, wifipass: str). Both are '' whenever the labels
        cannot be read, which is the case for any run not served a wifi secret.
    """
    store = machine.get('host_info_store')
    if store is None:
        return '', ''
    try:
        info = store.get()
    except Exception:
        # A corrupt or unreadable backing file. Never fatal: the caller has a
        # fallback, and no test should fail over a missing wifi label.
        logging.exception('Failed to read the host info store')
        return '', ''
    return (info.get_label_value(_SSID_LABEL),
            info.get_label_value(_PASSWORD_LABEL))


def get_wifi_ssid_pass(machine):
    """Retrieves Wifi SSID and password for current test run.

    Under CFT the credentials come from the DUT topology's wifi_secret, which
    reaches us as host info labels. The rest is the pre-CFT path, which reads
    global_config and is only usable in a lab whose SSID is baked into the
    autotest config.

    TODO(dadela): Drop the global_config lookup and the hardcoded fallback
    below once every pool serves a wifi_secret.

    Args:
        machine: The machine dict the control file was invoked with.

    Returns:
        A tuple (ssid: str, wifipass: str).
    """
    ssid, wifipass = _get_wifi_from_host_info(machine)
    if ssid:
        return ssid, wifipass

    hostname = machine['hostname']
    logging.debug(
            'No wifi secret in the host info of %s, falling back to the '
            'pre-CFT wifi credentials lookup', hostname)
    ssid = utils.get_wireless_ssid(hostname)
    if hostname.startswith('chromeos8'):
        ssid = 'wl-ChromeOS_lab_AP'
    wifipass = global_config.global_config.get_config_value(
            'CLIENT', 'wireless_password', default=None)

    # HACK(b/309894984): workaround missing SSID/password under CFT
    if not ssid and utils.is_in_container():
        ssid = 'ChromeOS_lab_AP'
    if not wifipass and utils.is_in_container():
        try:
            wifipass = utils.run('gsutil',
                                 args=('cat', _GS_WIFI_PASSWORD_PATH),
                                 verbose=True).stdout.strip()
        except:
            logging.exception('Failed to obtain wifi password on GS')

    return ssid, wifipass

# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utility functions for AFE-based interactions.

NOTE: This module should only be used in the context of a running test. Any
      utilities that require accessing the AFE, should do so by creating
      their own instance of the AFE client and interact with it directly.
"""

import common
import logging
import traceback
import urlparse

from autotest_lib.client.common_lib import global_config
from autotest_lib.client.common_lib import error
from autotest_lib.server.cros import autoupdater
from autotest_lib.server.cros import provision
from autotest_lib.server import site_utils as server_utils
from autotest_lib.server.cros.dynamic_suite import constants as ds_constants
from autotest_lib.server.cros.dynamic_suite import tools

from chromite.lib import auto_updater
# TODO(crbug.com/1066686) remove this try/except when moblab is using more
# recent chromite.
try:
   from chromite.lib import auto_updater_transfer
except ImportError:
   pass
from chromite.lib import remote_access


_CONFIG = global_config.global_config
ENABLE_DEVSERVER_TRIGGER_AUTO_UPDATE = _CONFIG.get_config_value(
        'CROS', 'enable_devserver_trigger_auto_update', type=bool,
        default=False)

DEVICE_BASE_DIR = '/usr/local/tmp/au-provision'

def _host_in_lab(host):
    """Check if the host is in the lab and an object the AFE knows.

    This check ensures that autoserv and the host's current job is running
    inside a fully Autotest instance, aka a lab environment. If this is the
    case it then verifies the host is registed with the configured AFE
    instance.

    @param host: Host object to verify.

    @returns The host model object.
    """
    if not host.job or not host.job.in_lab:
        return False
    return host._afe_host


def _log_image_name(image_name):
    try:
        logging.debug("_log_image_name: image (%s)", image_name)
        server_utils.ParseBuildName(name=image_name)
    except Exception:
        logging.error(traceback.format_exc())


def _format_image_name(board, version):
    return "%s-release/%s" % (board, version)


def get_stable_cros_image_name_v2(host_info):
    """Retrieve the Chrome OS stable image name for a given board.

    @param host_info: a host_info_store object.

    @returns Name of a Chrome OS image to be installed in order to
            repair the given board.
    """
    if not host_info.cros_stable_version:
        raise error.AutoservError("No cros stable_version found"
                                  " in host_info_store.")

    logging.debug("Get cros stable_version for board: %s",
                  getattr(host_info, "board", None))
    out = _format_image_name(board=host_info.board,
                             version=host_info.cros_stable_version)
    _log_image_name(out)
    return out


def get_stable_firmware_version_v2(host_info):
    """Retrieve the stable firmware version for a given model.

    @param host_info: a host_info_store object.

    @returns A version of firmware to be installed via
             `chromeos-firmwareupdate` from a repair build.
    """
    logging.debug("Get firmware stable_version for model: %s",
                  getattr(host_info, "model", None))
    return host_info.firmware_stable_version


def get_stable_faft_version_v2(host_info):
    """Retrieve the stable firmware version for FAFT DUTs.

    @param host_info: a host_info_store object.

    @returns A version of firmware to be installed in order to
            repair firmware on a DUT used for FAFT testing.
    """
    logging.debug("Get faft stable_version for model: %s",
                  getattr(host_info, "model", None))
    return host_info.faft_stable_version


def clean_provision_labels(host):
    """Clean provision-related labels.

    @param host: Host object.
    """
    info = host.host_info_store.get()
    info.clear_version_labels()
    attributes = host.get_attributes_to_clear_before_provision()
    for key in attributes:
      info.attributes.pop(key, None)

    host.host_info_store.commit(info)


def add_provision_labels(host, version_prefix, image_name,
                         provision_attributes={}):
    """Add provision labels for host.

    @param host: Host object.
    @param version_prefix: a string version prefix, e.g. "cros-version:"
    @param image_name: a string image name, e.g. peppy-release/R70-11011.0.0.
    @param provision_attributes: a map, including attributes for provisioning,
        e.g. {"job_repo_url": "http://..."}
    """
    info = host.host_info_store.get()
    info.attributes.update(provision_attributes)
    info.set_version_label(version_prefix, image_name)
    host.host_info_store.commit(info)


def machine_install_and_update_labels(host, update_url,
                                      use_quick_provision=False,
                                      with_cheets=False, staging_server=None):
    """Install a build and update the version labels on a host.

    @param host: Host object where the build is to be installed.
    @param update_url: URL of the build to install.
    @param use_quick_provision:  If true, then attempt to use
        quick-provision for the update.
    @param with_cheets: If true, installation is for a specific, custom
        version of Android for a target running ARC.
    @param staging_server: Server where images have been staged. Typically,
        an instance of dev_server.ImageServer.
    """
    clean_provision_labels(host)

    if use_quick_provision:
        image_name, host_attributes = _provision_with_quick_provision(
            host, update_url)
    else:
        image_name, host_attributes = _provision_with_au(host, update_url,
                                                         staging_server)

    if with_cheets:
        image_name += provision.CHEETS_SUFFIX
    add_provision_labels(host, host.VERSION_PREFIX, image_name, host_attributes)

def _provision_with_au(host, update_url, staging_server):
    """Installs a build on the host using chromite ChromiumOSUpdater.

    @param host: Host object where the build is to be installed.
    @param update_url: URL of the build to install.
    @param staging_server: Server where images have been staged. Typically,
        an instance of dev_server.ImageServer.

    @returns A tuple of the form `(image_name, host_attributes)`, where
        'image_name' is the name of the image installed, and 'host_attributes'
        are new attributes to be applied to the DUT.
    """
    logging.debug("Attempting to provision with Chromite ChromiumOSUpdater.")
    # Get image_name in the format <board>-release/Rxx-12345.0.0 from the
    # update_url.
    image_name = '/'.join(urlparse.urlparse(update_url).path.split('/')[-2:])
    with remote_access.ChromiumOSDeviceHandler(
          host.ip, base_dir=DEVICE_BASE_DIR) as device:
        updater = auto_updater.ChromiumOSUpdater(
            device, None, image_name, auto_updater_transfer.LabTransfer,
            staging_server=staging_server.url(), reboot=True,
            clear_tpm_owner=True)
        updater.RunUpdate()
    repo_url = tools.get_package_url(staging_server.url(), image_name)
    host_attributes = {ds_constants.JOB_REPO_URL: repo_url}
    return image_name, host_attributes

def _provision_with_quick_provision(host, update_url):
    """Installs a build on the host using autoupdater quick-provision.

    @param host: Host object where the build is to be installed.
    @param update_url: URL of the build to install.

    @returns A tuple of the form `(image_name, host_attributes)`, where
        'image_name' is the name of the image installed, and 'host_attributes'
        are new attributes to be applied to the DUT.
    """
    logging.debug('Attempting to provision with autoupdater quick-provision.')
    updater = autoupdater.ChromiumOSUpdater(update_url, host=host,
                                            use_quick_provision=True)
    return updater.run_update()

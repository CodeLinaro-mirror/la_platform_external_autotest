# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from autotest_lib.client.bin import utils
from autotest_lib.client.common_lib import error
from autotest_lib.client.common_lib.cros import chrome
from autotest_lib.client.cros.update_engine import nebraska_wrapper
from autotest_lib.client.cros.update_engine import update_engine_test
from telemetry.core import exceptions

class autoupdate_UpdateFromUI(update_engine_test.UpdateEngineTest):
    """Starts an update from the Chrome OS Settings app. """
    version = 1

    _NOTIFICATION_TITLE = "Update available"
    _NOTIFICATION_TIMEOUT = 10
    _NOTIFICATION_INTERVAL = 1


    def initialize(self):
        """Test setup."""
        super(autoupdate_UpdateFromUI, self).initialize()
        self._clear_custom_lsb_release()


    def cleanup(self):
        """Test cleanup. Clears the custom lsb-release used by the test. """
        self._clear_custom_lsb_release()
        super(autoupdate_UpdateFromUI, self).cleanup()

    def _wait_for_update_notification(self, cr):
        """
        Waits for the post-update notification to appear.

        @param cr: Chrome instance.

        """

        def find_notification():
            """Polls for visibility of the post-update notification. """
            notifications = cr.get_visible_notifications()
            if notifications is None:
                return False
            return any(n for n in notifications
                       if self._NOTIFICATION_TITLE in n['title'])

        utils.poll_for_condition(
                condition=find_notification,
                desc='Post-update notification not found',
                timeout=self._NOTIFICATION_TIMEOUT,
                sleep_interval=self._NOTIFICATION_INTERVAL)


    def run_once(self, payload_url):
        """
        Tests that a Chrome OS software update can be completed from the UI.

        @param payload_url: The payload url to use.

        """
        with nebraska_wrapper.NebraskaWrapper(
            log_dir=self.resultsdir, payload_url=payload_url) as nebraska:
            with chrome.Chrome(autotest_ext=True) as cr:
                # Need to create a custom lsb-release file to point the UI
                # update button to Nebraska instead of the default update
                # server.
                self._create_custom_lsb_release(
                    nebraska.get_update_url(critical_update=True))

                # Go to the OS settings page and check for an update.
                tab = cr.browser.tabs[0]
                tab.Navigate('chrome://os-settings/help')
                tab.WaitForDocumentReadyStateToBeComplete()
                self._take_screenshot('before_check_for_updates.png')
                try:
                    tab.EvaluateJavaScript('settings.AboutPageBrowserProxyImpl'
                                           '.getInstance().requestUpdate()')
                except exceptions.EvaluateException:
                    raise error.TestFail(
                        'Failed to find and click Check For Updates button.')
                self._take_screenshot('after_check_for_updates.png')
                self._wait_for_update_to_complete()
                self._wait_for_update_notification(cr)

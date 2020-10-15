# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Advertisement Monitor Test Application."""

import dbus
import dbus.mainloop.glib
import dbus.service
import gobject
import logging

from threading import Thread

DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'

BLUEZ_SERVICE_NAME = 'org.bluez'
BLUEZ_MANAGER_PATH = '/'

ADV_MONITOR_MANAGER_IFACE = 'org.bluez.AdvertisementMonitorManager1'
ADV_MONITOR_IFACE = 'org.bluez.AdvertisementMonitor1'
ADV_MONITOR_APP_BASE_PATH = '/org/bluez/adv_monitor_app'


class AdvMonitor(dbus.service.Object):
    """A monitor object.

    This class exposes a dbus monitor object along with its properties
    and methods.

    More information can be found at BlueZ documentation:
    doc/advertisement-monitor-api.txt

    """

    # Indexes of the Monitor object parameters in a monitor data list.
    MONITOR_TYPE = 0
    RSSI_FILTER = 1
    PATTERNS = 2

    # Indexes of the RSSI filter parameters in a monitor data list.
    RSSI_H_THRESH = 0
    RSSI_H_TIMEOUT = 1
    RSSI_L_THRESH = 2
    RSSI_L_TIMEOUT = 3

    # Indexes of the Patterns filter parameters in a monitor data list.
    PATTERN_START_POS = 0
    PATTERN_AD_TYPE = 1
    PATTERN_DATA = 2

    def __init__(self, bus, app_path, monitor_id, monitor_data):
        """Construction of a Monitor object.

        @param bus: a dbus system bus.
        @param app_path: application path.
        @param monitor_id: unique monitor id.

        """
        self.path = app_path + '/monitor' + str(monitor_id)
        self.bus = bus

        self.events = dict()
        self.events['Activate'] = 0
        self.events['Release'] = 0
        self.events['DeviceFound'] = 0
        self.events['DeviceLost'] = 0

        self._set_type(monitor_data[self.MONITOR_TYPE])
        self._set_rssi(monitor_data[self.RSSI_FILTER])
        self._set_patterns(monitor_data[self.PATTERNS])

        super(AdvMonitor, self).__init__(self.bus, self.path)


    def get_path(self):
        """Get the dbus object path of the monitor.

        @returns: the monitor object path.

        """
        return dbus.ObjectPath(self.path)


    def get_properties(self):
        """Get the properties dictionary of the monitor.

        @returns: the monitor properties dictionary.

        """
        properties = dict()
        properties['Type'] = dbus.String(self.monitor_type)
        properties['RSSIThresholdsAndTimers'] = dbus.Struct(self.rssi,
                                                            signature='nqnq')
        properties['Patterns'] = dbus.Array(self.patterns, signature='(yyay)')
        return {ADV_MONITOR_IFACE: properties}


    def _set_type(self, monitor_type):
        """Set the monitor type.

        @param monitor_type: the type of a monitor.

        """
        self.monitor_type = monitor_type


    def _set_rssi(self, rssi):
        """Set the RSSI filter values.

        @param rssi: the list of rssi threshold and timeout values.

        """
        h_thresh = dbus.Int16(rssi[self.RSSI_H_THRESH])
        h_timeout = dbus.UInt16(rssi[self.RSSI_H_TIMEOUT])
        l_thresh = dbus.Int16(rssi[self.RSSI_L_THRESH])
        l_timeout = dbus.UInt16(rssi[self.RSSI_L_TIMEOUT])
        self.rssi = (h_thresh, h_timeout, l_thresh, l_timeout)


    def _set_patterns(self, patterns):
        """Set the content filter patterns.

        @param patterns: the list of start position, ad type and patterns.

        """
        self.patterns = []
        for pattern in patterns:
            start_pos = dbus.Byte(pattern[self.PATTERN_START_POS])
            ad_type = dbus.Byte(pattern[self.PATTERN_AD_TYPE])
            ad_data = []
            for byte in pattern[self.PATTERN_DATA]:
                ad_data.append(dbus.Byte(byte))
            adv_pattern = dbus.Struct((start_pos, ad_type, ad_data),
                                      signature='yyay')
            self.patterns.append(adv_pattern)


    def remove_monitor(self):
        """Remove the monitor object.

        Invoke the dbus method to remove current monitor object from the
        connection.

        """
        self.remove_from_connection()


    def _update_event_count(self, event):
        """Update the event count.

        @param event: name of the event.

        """
        self.events[event] += 1


    def get_event_count(self, event):
        """Read the event count.

        @param event: name of the specific event or 'All' for all events.

        @returns: count of the specific event or dict of counts of all events.

        """
        if event == 'All':
            return self.events

        return self.events.get(event)


    def reset_event_count(self, event):
        """Reset the event count.

        @param event: name of the specific event or 'All' for all events.

        @returns: True on success, False otherwise.

        """
        if event == 'All':
            for event_key in self.events:
                self.events[event_key] = 0
            return True

        if event in self.events:
            self.events[event] = 0
            return True

        return False


    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        """Get the properties dictionary of the monitor.

        @param interface: the bluetooth dbus interface.

        @returns: the monitor properties dictionary.

        """
        logging.info('%s: %s GetAll', self.path, interface)

        if interface != ADV_MONITOR_IFACE:
            logging.error('%s: GetAll: Invalid arg %s', self.path, interface)
            return {}

        return self.get_properties()[ADV_MONITOR_IFACE]


    @dbus.service.method(ADV_MONITOR_IFACE,
                         in_signature='',
                         out_signature='')
    def Activate(self):
        """The method callback at Activate."""
        logging.info('%s: Monitor Activated!', self.path)
        self._update_event_count('Activate')


    @dbus.service.method(ADV_MONITOR_IFACE,
                         in_signature='',
                         out_signature='')
    def Release(self):
        """The method callback at Release."""
        logging.info('%s: Monitor Released!', self.path)
        self._update_event_count('Release')


    @dbus.service.method(ADV_MONITOR_IFACE,
                         in_signature='o',
                         out_signature='')
    def DeviceFound(self, device):
        """The method callback at DeviceFound.

        @param device: the dbus object path of the found device.

        """
        logging.info('%s: %s Device Found!', self.path, device)
        self._update_event_count('DeviceFound')


    @dbus.service.method(ADV_MONITOR_IFACE,
                         in_signature='o',
                         out_signature='')
    def DeviceLost(self, device):
        """The method callback at DeviceLost.

        @param device: the dbus object path of the lost device.

        """
        logging.info('%s: %s Device Lost!', self.path, device)
        self._update_event_count('DeviceLost')


class AdvMonitorApp(dbus.service.Object):
    """The test application.

    This class implements a test application to manage monitor objects.

    """

    def __init__(self, bus, dbus_mainloop, advmon_manager, app_id):
        """Construction of a test application object.

        @param bus: a dbus system bus.
        @param dbus_mainloop: an instance of mainloop.
        @param advmon_manager: AdvertisementMonitorManager1 interface on
                               the adapter.
        @param app_id: application id (to create application path).

        """
        self.bus = bus
        self.mainloop = dbus_mainloop
        self.advmon_mgr = advmon_manager

        self.app_path = ADV_MONITOR_APP_BASE_PATH + str(app_id)
        self.root_path = BLUEZ_MANAGER_PATH

        self.monitors = dict()

        # BlueZ expects InterfacesAdded and InterfacesRemoved signals to be
        # emitted on the root path. So, register the App object with root path
        # to be able to emit those signals correctly.
        super(AdvMonitorApp, self).__init__(self.bus, self.root_path)


    def get_app_path(self):
        """Get the dbus object path of the application.

        @returns: the application path.

        """
        return dbus.ObjectPath(self.app_path)


    def add_monitor(self, monitor_data):
        """Create a monitor object.

        @param monitor_data: the list containing monitor type, RSSI filter
                             values and patterns.

        @returns: monitor id, once the monitor is created.

        """
        monitor_id = 0
        while monitor_id in self.monitors:
            monitor_id += 1

        monitor = AdvMonitor(self.bus, self.app_path, monitor_id, monitor_data)

        # Emit the InterfacesAdded signal once the Monitor object is created.
        self.InterfacesAdded(monitor.get_path(), monitor.get_properties())

        self.monitors[monitor_id] = monitor

        return monitor_id


    def remove_monitor(self, monitor_id):
        """Remove a monitor object based on the given monitor id.

        @param monitor_id: the monitor id.

        @returns: True on success, False otherwise.

        """
        if monitor_id not in self.monitors:
            return False

        monitor = self.monitors[monitor_id]

        # Emit the InterfacesRemoved signal before removing the Monitor object.
        self.InterfacesRemoved(monitor.get_path(),
                               monitor.get_properties().keys())

        monitor.remove_monitor()

        self.monitors.pop(monitor_id)

        return True


    def get_event_count(self, monitor_id, event):
        """Read the count of a particular event on the given monitor.

        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: count of the specific event or dict of counts of all events.

        """
        if monitor_id not in self.monitors:
            return None

        return self.monitors[monitor_id].get_event_count(event)


    def reset_event_count(self, monitor_id, event):
        """Reset the count of a particular event on the given monitor.

        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: True on success, False otherwise.

        """
        if monitor_id not in self.monitors:
            return False

        return self.monitors[monitor_id].reset_event_count(event)


    def _mainloop_thread(self):
        """Run the dbus mainloop thread.

        Callback methods on the monitor objects get invoked only when the
        dbus mainloop is running. This thread starts when app is registered
        and stops when app is unregistered.

        """
        self.mainloop.run() # blocks until mainloop.quit() is called


    def register_app(self):
        """Register an advertisement monitor app.

        @returns: True on success, False otherwise.

        """
        if self.mainloop.is_running():
            self.mainloop.quit()

        self.register_successful = False

        def register_cb():
            """Handler when RegisterMonitor succeeded."""
            logging.info('%s: RegisterMonitor successful!', self.app_path)
            self.register_successful = True
            self.mainloop.quit()

        def register_error_cb(error):
            """Handler when RegisterMonitor failed."""
            logging.error('%s: RegisterMonitor failed: %s', self.app_path,
                                                            str(error))
            self.register_successful = False
            self.mainloop.quit()

        self.advmon_mgr.RegisterMonitor(self.get_app_path(),
                                        reply_handler=register_cb,
                                        error_handler=register_error_cb)
        self.mainloop.run() # blocks until mainloop.quit() is called

        # Start a background thread to run mainloop.run(). This is required for
        # the bluetoothd to be able to invoke methods on the monitor object.
        # Mark this thread as a daemon to make sure that the thread is killed
        # in case the parent process dies unexpectedly.
        t = Thread(target=self._mainloop_thread)
        t.daemon = True
        t.start()

        return self.register_successful


    def unregister_app(self):
        """Unregister an advertisement monitor app.

        @returns: True on success, False otherwise.

        """
        if self.mainloop.is_running():
            self.mainloop.quit()

        self.unregister_successful = False

        def unregister_cb():
            """Handler when UnregisterMonitor succeeded."""
            logging.info('%s: UnregisterMonitor successful!', self.app_path)
            self.unregister_successful = True
            self.mainloop.quit()

        def unregister_error_cb(error):
            """Handler when UnregisterMonitor failed."""
            logging.error('%s: UnregisterMonitor failed: %s', self.app_path,
                                                              str(error))
            self.unregister_successful = False
            self.mainloop.quit()

        self.advmon_mgr.UnregisterMonitor(self.get_app_path(),
                                          reply_handler=unregister_cb,
                                          error_handler=unregister_error_cb)
        self.mainloop.run() # blocks until mainloop.quit() is called

        return self.unregister_successful


    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        """Get the list of managed monitor objects.

        @returns: the list of managed objects and their properties.

        """
        logging.info('%s: GetManagedObjects', self.app_path)

        objects = dict()
        for monitor_id in self.monitors:
            monitor = self.monitors[monitor_id]
            objects[monitor.get_path()] = monitor.get_properties()

        return objects


    @dbus.service.signal(DBUS_OM_IFACE, signature='oa{sa{sv}}')
    def InterfacesAdded(self, object_path, interfaces_and_properties):
        """Emit the InterfacesAdded signal for a given monitor object.

        Invoking this method emits the InterfacesAdded signal,
        nothing needs to be done here.

        @param object_path: the dbus object path of a monitor.
        @param interfaces_and_properties: the monitor properties dictionary.

        """
        return


    @dbus.service.signal(DBUS_OM_IFACE, signature='oas')
    def InterfacesRemoved(self, object_path, interfaces):
        """Emit the InterfacesRemoved signal for a given monitor object.

        Invoking this method emits the InterfacesRemoved signal,
        nothing needs to be done here.

        @param object_path: the dbus object path of a monitor.
        @param interfaces: the list of monitor interfaces.

        """
        return


class AdvMonitorAppMgr():
    """The app manager for Advertisement Monitor Test Apps.

    This class manages instances of multiple advertisement monitor test
    applications.

    """

    def __init__(self, bus, dbus_mainloop, advmon_manager):
        """Construction of applications manager object.

        @param bus: a dbus system bus.
        @param dbus_mainloop: an instance of mainloop.
        @param advmon_manager: AdvertisementMonitorManager1 interface on
                               the adapter.

        """
        self.bus = bus
        self.mainloop = dbus_mainloop
        self.advmon_mgr = advmon_manager

        self.apps = dict()

        # Initialize threads in gobject/dbus-glib before creating local threads.
        gobject.threads_init()
        dbus.mainloop.glib.threads_init()


    def create_app(self):
        """Create an advertisement monitor app.

        @returns: app id, once the app is created.

        """
        app_id = 0

        # TODO(b/162791635) - fork a new app instance instead of creating a
        # local app object (to be implemented with multiclient implementation).
        if app_id not in self.apps:
            app = AdvMonitorApp(self.bus,
                                self.mainloop,
                                self.advmon_mgr,
                                app_id)
            self.apps[app_id] = app

        return app_id


    def exit_app(self, app_id):
        """Exit an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """

        # TODO(b/162791635) - to be implemented with multiclient implementation.

        return True


    def kill_app(self, app_id):
        """Kill an advertisement monitor app by sending SIGKILL.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """

        # TODO(b/162791635) - to be implemented with multiclient implementation.

        return True


    def register_app(self, app_id):
        """Register an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        if app_id not in self.apps:
            return False

        return self.apps[app_id].register_app()


    def unregister_app(self, app_id):
        """Unregister an advertisement monitor app.

        @param app_id: the app id.

        @returns: True on success, False otherwise.

        """
        if app_id not in self.apps:
            return False

        return self.apps[app_id].unregister_app()


    def add_monitor(self, app_id, monitor_data):
        """Create a monitor object.

        @param app_id: the app id.
        @param monitor_data: the list containing monitor type, RSSI filter
                             values and patterns.

        @returns: monitor id, once the monitor is created, None otherwise.

        """
        if app_id not in self.apps:
            return None

        return self.apps[app_id].add_monitor(monitor_data)


    def remove_monitor(self, app_id, monitor_id):
        """Remove a monitor object based on the given monitor id.

        @param app_id: the app id.
        @param monitor_id: the monitor id.

        @returns: True on success, False otherwise.

        """
        if app_id not in self.apps:
            return False

        return self.apps[app_id].remove_monitor(monitor_id)


    def get_event_count(self, app_id, monitor_id, event):
        """Read the count of a particular event on the given monitor.

        @param app_id: the app id.
        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: count of the specific event or dict of counts of all events.

        """
        if app_id not in self.apps:
            return None

        return self.apps[app_id].get_event_count(monitor_id, event)


    def reset_event_count(self, app_id, monitor_id, event):
        """Reset the count of a particular event on the given monitor.

        @param app_id: the app id.
        @param monitor_id: the monitor id.
        @param event: name of the specific event or 'All' for all events.

        @returns: True on success, False otherwise.

        """
        if app_id not in self.apps:
            return False

        return self.apps[app_id].reset_event_count(monitor_id, event)

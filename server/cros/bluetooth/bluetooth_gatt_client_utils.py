# Lint as: python2, python3
# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Server side bluetooth GATT client helper class for testing"""

import base64
import json
from uuid import UUID


class GATT_ClientFacade(object):
    """A wrapper for getting GATT application from GATT server"""

    def __init__(self, bluetooth_facade):
        """Initialize a GATT_ClientFacade

        @param bluetooth_facade: facade to communicate with adapter in DUT

        """
        self.bluetooth_facade = bluetooth_facade


    def browse(self, address):
        """Browse the application on GATT server

        @param address: a string of MAC address of the GATT server device

        @return: GATT_Application object

        """
        attr_map_json = json.loads(self.bluetooth_facade.\
                              get_gatt_attributes_map(address))
        application = GATT_Application()
        application.browse(attr_map_json, self.bluetooth_facade)

        return application


def UUID_Short2Full(uuid):
    """Transforms 2-byte UUID string to 16-byte UUID string.
     It transforms the 2-byte UUID by inserting it into a fixed template
     16-byte UUID string.

    @param uuid: 2-byte shortened UUID string in hex.

    @return: Full 16-byte UUID string.
    """
    uuid_template = '0000%s-0000-1000-8000-00805f9b34fb'
    return uuid_template % uuid


class GATT_Application(object):
    """A GATT client application class"""

    BATTERY_SERVICE_UUID = UUID_Short2Full('180f')
    BATTERY_LEVEL_UUID = UUID_Short2Full('2a19')
    CLI_CHRC_CONFIG_UUID = UUID_Short2Full('2902')
    GENERIC_ATTRIBUTE_PROFILE_UUID = UUID_Short2Full('1801')
    SERVICE_CHANGED_UUID = UUID_Short2Full('2a05')
    DEVICE_INFO_UUID = UUID_Short2Full('180a')
    MANUFACTURER_NAME_STR_UUID = UUID_Short2Full('2a29')
    PNP_ID_UUID = UUID_Short2Full('2a50')
    GENERIC_ACCESS_PROFILE_UUID = UUID_Short2Full('1800')
    DEVICE_NAME_UUID = UUID_Short2Full('2a00')
    APPEARANCE_UUID = UUID_Short2Full('2a01')
    HID_SERVICE_UUID = UUID_Short2Full('1812')
    REPORT_UUID = UUID_Short2Full('2a4d')
    REPORT_REFERENCE_UUID = UUID_Short2Full('2908')
    REPORT_MAP_UUID = UUID_Short2Full('2a4b')
    HID_INFORMATION_UUID = UUID_Short2Full('2a4a')
    HID_CONTROL_POINT_UUID = UUID_Short2Full('2a4c')
    DATABASE_HASH_UUID = UUID_Short2Full('2b2a')
    CLIENT_SUPPORTED_FEATURES_UUID = UUID_Short2Full('2b29')
    CENTRAL_ADDRESS_RESOLUTION_UUID = UUID_Short2Full('2aa6')

    def __init__(self):
        """Initialize a GATT Application"""
        self.services = dict()

    def browse_floss(self, attr_map_json, bluetooth_facade):
        """Browse the application on GATT server.

        @param attr_map_json: A json object returned by
                              bluetooth_device_xmlrpc_server.
        @param bluetooth_facade: Facade to communicate with adapter in DUT.
        """
        for service in attr_map_json:
            path = None

            uuid = str(UUID(bytes=bytes(service['uuid'])))
            service_obj = GATT_Service(uuid, path, bluetooth_facade)
            service_obj.fill_floss_properties(service)
            self.add_service(service_obj)

            chrcs_json = service['characteristics']
            for charac in chrcs_json:
                path = None
                uuid = str(UUID(bytes=bytes(charac['uuid'])))
                chrc_obj = GATT_Characteristic(uuid, path, bluetooth_facade)
                chrc_obj.fill_floss_properties(charac)
                service_obj.add_characteristic(chrc_obj)
                descs_json = charac['descriptors']
                for desc in descs_json:
                    path = None
                    uuid = str(UUID(bytes=bytes(desc['uuid'])))
                    desc_obj = GATT_Descriptor(uuid, path, bluetooth_facade)
                    desc_obj.fill_floss_properties(desc)
                    chrc_obj.add_descriptor(desc_obj)

    def browse(self, attr_map_json, bluetooth_facade):
        """Browse the application on GATT server

        @param attr_map_json: a json object returned by
                              bluetooth_device_xmlrpc_server

        @bluetooth_facade: facade to communicate with adapter in DUT

        """
        if bluetooth_facade.floss:
            self.browse_floss(attr_map_json, bluetooth_facade)
            return
        servs_json = attr_map_json['services']
        for uuid in servs_json:
            path = servs_json[uuid]['path']
            service_obj = GATT_Service(uuid, path, bluetooth_facade)
            service_obj.read_properties()
            self.add_service(service_obj)

            chrcs_json = servs_json[uuid]['characteristics']
            for uuid in chrcs_json:
                path = chrcs_json[uuid]['path']
                chrc_obj = GATT_Characteristic(uuid, path, bluetooth_facade)
                chrc_obj.read_properties()
                service_obj.add_characteristic(chrc_obj)

                descs_json = chrcs_json[uuid]['descriptors']
                for uuid in descs_json:
                    path = descs_json[uuid]['path']
                    desc_obj = GATT_Descriptor(uuid, path, bluetooth_facade)
                    desc_obj.read_properties()
                    chrc_obj.add_descriptor(desc_obj)


    def find_by_uuid(self, uuid):
        """Find attribute under this application by specifying UUID

        @param uuid: string of UUID

        @return: Attribute object if found,
                 none otherwise
        """
        for serv_uuid, serv in self.services.items():
            found = serv.find_by_uuid(uuid)
            if found:
                return found
        return None


    def add_service(self, service):
        """Add a service into this application"""
        self.services[service.uuid] = service


    @staticmethod
    def diff(appl_a, appl_b):
        """Compare two Applications, and return their difference

        @param appl_a: the first application which is going to be compared

        @param appl_b: the second application which is going to be compared

        @return: a list of string, each describes one difference

        """
        result = []

        uuids_a = set(appl_a.services.keys())
        uuids_b = set(appl_b.services.keys())
        uuids = uuids_a.union(uuids_b)

        for uuid in uuids:
            serv_a = appl_a.services.get(uuid, None)
            serv_b = appl_b.services.get(uuid, None)

            if not serv_a or not serv_b:
                result.append("Service %s is not included in both Applications:"
                              "%s vs %s" % (uuid, bool(serv_a), bool(serv_b)))
            else:
                result.extend(GATT_Service.diff(serv_a, serv_b))
        return result


class GATT_Service(object):
    """GATT client service class"""
    PROPERTIES = ['UUID', 'Primary', 'Device', 'Includes']

    FLOSS_PROPERTIES = ['uuid', 'service_type', 'included_services']

    def __init__(self, uuid, object_path, bluetooth_facade):
        """Initialize a GATT service object

        @param uuid: string of UUID

        @param object_path: object path of this service

        @param bluetooth_facade: facade to communicate with adapter in DUT

        """
        self.uuid = uuid
        self.object_path = object_path
        self.bluetooth_facade = bluetooth_facade
        self.properties = dict()
        self.characteristics = dict()

    def fill_floss_properties(self, service):
        """Fill all properties in this service.

        @param service: GATT service.

        @return: GATT service properties.
        """
        for prop_name in self.FLOSS_PROPERTIES:
            if prop_name == 'uuid':
                self.properties[prop_name] = str(
                        UUID(bytes=bytes(service[prop_name])))
            else:
                self.properties[prop_name] = service[prop_name]
        return self.properties

    def add_characteristic(self, chrc_obj):
        """Add a characteristic attribute into service

        @param chrc_obj: a characteristic object

        """
        self.characteristics[chrc_obj.uuid] = chrc_obj


    def read_properties(self):
        """Read all properties in this service"""
        for prop_name in self.PROPERTIES:
            self.properties[prop_name] = self.read_property(prop_name)
        return self.properties


    def read_property(self, property_name):
        """Read a property in this service

        @param property_name: string of the name of the property

        @return: the value of the property

        """
        return self.bluetooth_facade.get_gatt_service_property(
                                        self.object_path, property_name)

    def find_by_uuid(self, uuid):
        """Find attribute under this service by specifying UUID

        @param uuid: string of UUID

        @return: Attribute object if found,
                 none otherwise

        """
        if self.uuid == uuid:
            return self

        for chrc_uuid, chrc in self.characteristics.items():
            found = chrc.find_by_uuid(uuid)
            if found:
                return found
        return None


    @staticmethod
    def diff(serv_a, serv_b):
        """Compare two Services, and return their difference

        @param serv_a: the first service which is going to be compared

        @param serv_b: the second service which is going to be compared

        @return: a list of string, each describes one difference

        """
        result = []

        for prop_name in serv_b.properties.keys():
            if serv_a.properties[prop_name] != serv_b.properties[prop_name]:
                result.append("Service %s is different in %s: %s vs %s" %
                              (serv_a.uuid, prop_name,
                              serv_a.properties[prop_name],
                              serv_b.properties[prop_name]))

        uuids_a = set(serv_a.characteristics.keys())
        uuids_b = set(serv_b.characteristics.keys())
        uuids = uuids_a.union(uuids_b)

        for uuid in uuids:
            chrc_a = serv_a.characteristics.get(uuid, None)
            chrc_b = serv_b.characteristics.get(uuid, None)

            if not chrc_a or not chrc_b:
                result.append("Characteristic %s is not included in both "
                              "Services: %s vs %s" % (uuid, bool(chrc_a),
                                                    bool(chrc_b)))
            else:
                result.extend(GATT_Characteristic.diff(chrc_a, chrc_b))
        return result


class GATT_Characteristic(object):
    """GATT client characteristic class"""

    PROPERTIES = ['UUID', 'Service', 'Value', 'Notifying', 'Flags']

    FLOSS_PROPERTIES = [
            'uuid', 'properties', 'permissions', 'write_type', 'key_size'
    ]

    def __init__(self, uuid, object_path, bluetooth_facade):
        """Initialize a GATT characteristic object

        @param uuid: string of UUID

        @param object_path: object path of this characteristic

        @param bluetooth_facade: facade to communicate with adapter in DUT

        """
        self.uuid = uuid
        self.object_path = object_path
        self.bluetooth_facade = bluetooth_facade
        self.properties = dict()
        self.descriptors = dict()

    def fill_floss_properties(self, char):
        """Fill all properties in this characteristic.

        @param char: GATT characteristic.

        @return: GATT characteristic properties.
        """
        for prop_name in self.FLOSS_PROPERTIES:
            if prop_name == 'uuid':
                self.properties[prop_name] = str(
                        UUID(bytes=bytes(char[prop_name])))
            else:
                self.properties[prop_name] = char[prop_name]
        return self.properties

    def add_descriptor(self, desc_obj):
        """Add a characteristic attribute into service

        @param desc_obj: a descriptor object

        """
        self.descriptors[desc_obj.uuid] = desc_obj


    def read_properties(self):
        """Read all properties in this characteristic"""
        for prop_name in self.PROPERTIES:
            self.properties[prop_name] = self.read_property(prop_name)
        return self.properties


    def read_property(self, property_name):
        """Read a property in this characteristic

        @param property_name: string of the name of the property

        @return: the value of the property

        """
        return self.bluetooth_facade.get_gatt_characteristic_property(
                                        self.object_path, property_name)


    def find_by_uuid(self, uuid):
        """Find attribute under this characteristic by specifying UUID

        @param uuid: string of UUID

        @return: Attribute object if found,
                 none otherwise

        """
        if self.uuid == uuid:
            return self

        for desc_uuid, desc in self.descriptors.items():
            if desc_uuid == uuid:
                return desc
        return None


    def read_value(self):
        """Perform ReadValue in DUT and store it in property 'Value'

        @return: bytearray of the value

        """
        value = self.bluetooth_facade.gatt_characteristic_read_value(
                                                self.uuid, self.object_path)
        self.properties['Value'] = bytearray(base64.standard_b64decode(value))
        return self.properties['Value']


    @staticmethod
    def diff(chrc_a, chrc_b):
        """Compare two Characteristics, and return their difference

        @param serv_a: the first service which is going to be compared

        @param serv_b: the second service which is going to be compared

        @return: a list of string, each describes one difference

        """
        result = []

        for prop_name in chrc_b.properties.keys():
            if chrc_a.properties[prop_name] != chrc_b.properties[prop_name]:
                result.append("Characteristic %s is different in %s: %s vs %s"
                              % (chrc_a.uuid, prop_name,
                              chrc_a.properties[prop_name],
                              chrc_b.properties[prop_name]))

        uuids_a = set(chrc_a.descriptors.keys())
        uuids_b = set(chrc_b.descriptors.keys())
        uuids = uuids_a.union(uuids_b)

        for uuid in uuids:
            desc_a = chrc_a.descriptors.get(uuid, None)
            desc_b = chrc_b.descriptors.get(uuid, None)

            if not desc_a or not desc_b:
                result.append("Descriptor %s is not included in both"
                              "Characteristic: %s vs %s" % (uuid, bool(desc_a),
                                                          bool(desc_b)))
            else:
                result.extend(GATT_Descriptor.diff(desc_a, desc_b))
        return result


class GATT_Descriptor(object):
    """GATT client descriptor class"""

    PROPERTIES = ['UUID', 'Characteristic', 'Value', 'Flags']

    FLOSS_PROPERTIES = ['uuid', 'permissions']

    def __init__(self, uuid, object_path, bluetooth_facade):
        """Initialize a GATT descriptor object

        @param uuid: string of UUID

        @param object_path: object path of this descriptor

        @param bluetooth_facade: facade to communicate with adapter in DUT

        """
        self.uuid = uuid
        self.object_path = object_path
        self.bluetooth_facade = bluetooth_facade
        self.properties = dict()

    def fill_floss_properties(self, desc):
        """Fill all properties in this descriptor.

        @param desc: GATT descriptor.

        @return: GATT descriptor properties.
        """
        for prop_name in self.FLOSS_PROPERTIES:
            if prop_name == 'uuid':
                self.properties[prop_name] = str(
                        UUID(bytes=bytes(desc[prop_name])))
            else:
                self.properties[prop_name] = desc[prop_name]
        return self.properties

    def read_properties(self):
        """Read all properties in this characteristic"""
        for prop_name in self.PROPERTIES:
            self.properties[prop_name] = self.read_property(prop_name)
        return self.properties


    def read_property(self, property_name):
        """Read a property in this characteristic

        @param property_name: string of the name of the property

        @return: the value of the property

        """
        return self.bluetooth_facade.get_gatt_descriptor_property(
                                        self.object_path, property_name)


    def read_value(self):
        """Perform ReadValue in DUT and store it in property 'Value'

        @return: bytearray of the value

        """
        value = self.bluetooth_facade.gatt_descriptor_read_value(
                                                self.uuid, self.object_path)
        self.properties['Value'] = bytearray(base64.standard_b64decode(value))

        return self.properties['Value']


    @staticmethod
    def diff(desc_a, desc_b):
        """Compare two Descriptors, and return their difference

        @param serv_a: the first service which is going to be compared

        @param serv_b: the second service which is going to be compared

        @return: a list of string, each describes one difference

        """
        result = []

        for prop_name in desc_a.properties.keys():
            if desc_a.properties[prop_name] != desc_b.properties[prop_name]:
                result.append("Descriptor %s is different in %s: %s vs %s" %
                              (desc_a.uuid, prop_name,
                              desc_a.properties[prop_name],
                              desc_b.properties[prop_name]))

        return result




class GATT_HIDApplication(GATT_Application):
    """Default HID Application on Raspberry Pi GATT server
    """
    def __init__(self):
        """
        """
        GATT_Application.__init__(self)
        BatteryService = GATT_Service(self.BATTERY_SERVICE_UUID, None, None)
        BatteryService.properties = {
                'UUID': BatteryService.uuid,
                'Primary': True,
                'Device': None,
                'Includes': []
        }
        self.add_service(BatteryService)

        BatteryLevel = GATT_Characteristic(self.BATTERY_LEVEL_UUID, None, None)
        BatteryLevel.properties = {
                'UUID': BatteryLevel.uuid,
                'Service': None,
                'Value': [],
                'Notifying': False,
                'Flags': ['read', 'notify']
        }
        BatteryService.add_characteristic(BatteryLevel)

        CliChrcConfig = GATT_Descriptor(self.CLI_CHRC_CONFIG_UUID, None, None)
        CliChrcConfig.properties = {
                'UUID': CliChrcConfig.uuid,
                'Characteristic': None,
                'Value': [],
                'Flags': None
        }

        BatteryLevel.add_descriptor(CliChrcConfig)

        GenericAttributeProfile = GATT_Service(
                self.GENERIC_ATTRIBUTE_PROFILE_UUID, None, None)
        GenericAttributeProfile.properties = {
                'UUID': GenericAttributeProfile.uuid,
                'Primary': True,
                'Device': None,
                'Includes': []
        }
        self.add_service(GenericAttributeProfile)

        ServiceChanged = GATT_Characteristic(self.SERVICE_CHANGED_UUID, None,
                                             None)
        ServiceChanged.properties = {
                'UUID': ServiceChanged.uuid,
                'Service': None,
                'Value': [],
                'Notifying': False,
                'Flags': ['indicate']
        }
        GenericAttributeProfile.add_characteristic(ServiceChanged)

        CliChrcConfig = GATT_Descriptor(self.CLI_CHRC_CONFIG_UUID, None, None)
        CliChrcConfig.properties = {
                'UUID': CliChrcConfig.uuid,
                'Characteristic': None,
                'Value': [],
                'Flags': None
        }
        ServiceChanged.add_descriptor(CliChrcConfig)

        DeviceInfo = GATT_Service(self.DEVICE_INFO_UUID, None, None)
        DeviceInfo.properties = {
                'UUID': DeviceInfo.uuid,
                'Primary': True,
                'Device': None,
                'Includes': []
        }
        self.add_service(DeviceInfo)

        ManufacturerNameStr = GATT_Characteristic(
                self.MANUFACTURER_NAME_STR_UUID, None, None)
        ManufacturerNameStr.properties = {
                'UUID': ManufacturerNameStr.uuid,
                'Service': None,
                'Value': [],
                'Notifying': None,
                'Flags': ['read']
        }
        DeviceInfo.add_characteristic(ManufacturerNameStr)

        PnPID = GATT_Characteristic(self.PNP_ID_UUID, None, None)
        PnPID.properties = {
                'UUID': PnPID.uuid,
                'Service': None,
                'Value': [],
                'Notifying': None,
                'Flags': ['read']
        }
        DeviceInfo.add_characteristic(PnPID)

        GenericAccessProfile = GATT_Service(self.GENERIC_ACCESS_PROFILE_UUID,
                                            None, None)
        GenericAccessProfile.properties = {
                'UUID': GenericAccessProfile.uuid,
                'Primary': True,
                'Device': None,
                'Includes': []
        }
        self.add_service(GenericAccessProfile)

        DeviceName = GATT_Characteristic(self.DEVICE_NAME_UUID, None, None)
        DeviceName.properties = {
                'UUID': DeviceName.uuid,
                'Service': None,
                'Value': [],
                'Notifying': None,
                'Flags': ['read']
        }
        GenericAccessProfile.add_characteristic(DeviceName)

        Appearance = GATT_Characteristic(self.APPEARANCE_UUID, None, None)
        Appearance.properties = {
                'UUID': Appearance.uuid,
                'Service': None,
                'Value': [],
                'Notifying': None,
                'Flags': ['read']
        }
        GenericAccessProfile.add_characteristic(Appearance)


class Floss_GATT_HIDApplication(GATT_Application):
    """Default Floss HID Application on Raspberry Pi GATT server."""

    def __init__(self, is_bookworm=False):
        """
        """
        GATT_Application.__init__(self)

        HIDService = GATT_Service(self.HID_SERVICE_UUID, None, None)
        HIDService.properties = {
                'uuid': HIDService.uuid,
                'service_type': 0,
                'included_services': []
        }
        self.add_service(HIDService)

        Report = GATT_Characteristic(self.REPORT_UUID, None, None)
        Report.properties = {
                'uuid': Report.uuid,
                'properties': 18,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        HIDService.add_characteristic(Report)

        CliChrcConfig = GATT_Descriptor(self.CLI_CHRC_CONFIG_UUID, None, None)
        CliChrcConfig.properties = {
                'uuid': CliChrcConfig.uuid,
                'permissions': 0
        }
        Report.add_descriptor(CliChrcConfig)

        ReportReference = GATT_Descriptor(self.REPORT_REFERENCE_UUID, None,
                                          None)
        ReportReference.properties = {
                'uuid': ReportReference.uuid,
                'permissions': 0
        }
        Report.add_descriptor(ReportReference)

        ReportMap = GATT_Characteristic(self.REPORT_MAP_UUID, None, None)
        ReportMap.properties = {
                'uuid': ReportMap.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        HIDService.add_characteristic(ReportMap)

        HIDInformation = GATT_Characteristic(self.HID_INFORMATION_UUID, None,
                                             None)
        HIDInformation.properties = {
                'uuid': HIDInformation.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        HIDService.add_characteristic(HIDInformation)

        HIDControlPoint = GATT_Characteristic(self.HID_CONTROL_POINT_UUID,
                                              None, None)
        HIDControlPoint.properties = {
                'uuid': HIDControlPoint.uuid,
                'properties': 4,
                'key_size': 16,
                'write_type': 1,
                'permissions': 0
        }
        HIDService.add_characteristic(HIDControlPoint)

        BatteryService = GATT_Service(self.BATTERY_SERVICE_UUID, None, None)
        BatteryService.properties = {
                'uuid': BatteryService.uuid,
                'service_type': 0,
                'included_services': []
        }
        self.add_service(BatteryService)

        BatteryLevel = GATT_Characteristic(self.BATTERY_LEVEL_UUID, None, None)
        BatteryLevel.properties = {
                'uuid': BatteryLevel.uuid,
                'properties': 18,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        BatteryService.add_characteristic(BatteryLevel)

        CliChrcConfig = GATT_Descriptor(self.CLI_CHRC_CONFIG_UUID, None, None)
        CliChrcConfig.properties = {
                'uuid': CliChrcConfig.uuid,
                'permissions': 0
        }
        BatteryLevel.add_descriptor(CliChrcConfig)

        GenericAttributeProfile = GATT_Service(
                self.GENERIC_ATTRIBUTE_PROFILE_UUID, None, None)
        GenericAttributeProfile.properties = {
                'uuid': GenericAttributeProfile.uuid,
                'service_type': 0,
                'included_services': []
        }
        self.add_service(GenericAttributeProfile)

        ServiceChanged = GATT_Characteristic(self.SERVICE_CHANGED_UUID, None,
                                             None)
        ServiceChanged.properties = {
                'uuid': ServiceChanged.uuid,
                'properties': 32,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        GenericAttributeProfile.add_characteristic(ServiceChanged)

        CliChrcConfig = GATT_Descriptor(self.CLI_CHRC_CONFIG_UUID, None, None)
        CliChrcConfig.properties = {
                'uuid': CliChrcConfig.uuid,
                'permissions': 0
        }
        ServiceChanged.add_descriptor(CliChrcConfig)

        if is_bookworm:
            # Add new GATT Caching characteristics for Bookworm

            # Client Supported Features (0x2b29) - handle 0x000d (13)
            ClientSupportedFeatures = GATT_Characteristic(
                    self.CLIENT_SUPPORTED_FEATURES_UUID, None, None)
            ClientSupportedFeatures.properties = {
                    'uuid': ClientSupportedFeatures.uuid,
                    'properties': 10,  # Read, Write
                    'key_size': 16,
                    'write_type': 2,
                    'permissions': 0
            }
            GenericAttributeProfile.add_characteristic(ClientSupportedFeatures)

            # Database Hash (0x2b2a) - handle 0x000f (15)
            DatabaseHash = GATT_Characteristic(self.DATABASE_HASH_UUID, None,
                                               None)
            DatabaseHash.properties = {
                    'uuid': DatabaseHash.uuid,
                    'properties': 2,  # Read
                    'key_size': 16,
                    'write_type': 2,
                    'permissions': 0
            }
            GenericAttributeProfile.add_characteristic(DatabaseHash)


        DeviceInfo = GATT_Service(self.DEVICE_INFO_UUID, None, None)
        DeviceInfo.properties = {
                'uuid': DeviceInfo.uuid,
                'service_type': 0,
                'included_services': []
        }
        self.add_service(DeviceInfo)

        ManufacturerNameStr = GATT_Characteristic(
                self.MANUFACTURER_NAME_STR_UUID, None, None)
        ManufacturerNameStr.properties = {
                'uuid': ManufacturerNameStr.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        DeviceInfo.add_characteristic(ManufacturerNameStr)

        PnPID = GATT_Characteristic(self.PNP_ID_UUID, None, None)
        PnPID.properties = {
                'uuid': PnPID.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        DeviceInfo.add_characteristic(PnPID)
        GenericAccessProfile = GATT_Service(self.GENERIC_ACCESS_PROFILE_UUID,
                                            None, None)
        GenericAccessProfile.properties = {
                'uuid': GenericAccessProfile.uuid,
                'service_type': 0,
                'included_services': []
        }
        self.add_service(GenericAccessProfile)

        DeviceName = GATT_Characteristic(self.DEVICE_NAME_UUID, None, None)
        DeviceName.properties = {
                'uuid': DeviceName.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        GenericAccessProfile.add_characteristic(DeviceName)

        Appearance = GATT_Characteristic(self.APPEARANCE_UUID, None, None)
        Appearance.properties = {
                'uuid': Appearance.uuid,
                'properties': 2,
                'key_size': 16,
                'write_type': 2,
                'permissions': 0
        }
        GenericAccessProfile.add_characteristic(Appearance)

        if is_bookworm:
            # Central Address Resolution (0x2aa6) - handle 0x0007 (7)
            CentralAddrRes = GATT_Characteristic(
                    self.CENTRAL_ADDRESS_RESOLUTION_UUID, None, None)
            CentralAddrRes.properties = {
                    'uuid': CentralAddrRes.uuid,
                    'properties': 2,  # Read
                    'key_size': 16,
                    'write_type': 2,
                    'permissions': 0
            }
            GenericAccessProfile.add_characteristic(CentralAddrRes)

# NetBox Diode Plugin - Object Matching Criteria

This document describes how the Diode NetBox Plugin matches existing objects when applying changes.

## Matcher Types

- **Logical Matchers**: Custom matching criteria that represent likely user intent
- **Builtin Matchers**: Automatically generated from NetBox model constraints (unique fields, unique constraints, custom fields, auto-slugs)

## circuits.circuit

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_circuit_unique_provider_cid | builtin | provider, cid | N/A | Matches on unique constraint fields: provider, cid | All versions |
| circuits_circuit_unique_provideraccount_cid | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | All versions |

## circuits.circuitgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.circuitgroupassignment

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_circuitgroupassignment_unique_member_group | builtin | member_type, member_id, group | N/A | Matches on unique constraint fields: member_type, member_id, group | All versions |

## circuits.circuittermination

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_circuittermination_unique_circuit_term_side | builtin | circuit, term_side | N/A | Matches on unique constraint fields: circuit, term_side | All versions |

## circuits.circuittype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.provider

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.provideraccount

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_provideraccount_unique_provider_account | builtin | provider, account | N/A | Matches on unique constraint fields: provider, account | All versions |
| circuits_provideraccount_unique_provider_name | builtin | provider, name | name =  | Matches on unique constraint fields: provider, name where name =  | All versions |

## circuits.providernetwork

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_providernetwork_unique_provider_name | builtin | provider, name | N/A | Matches on unique constraint fields: provider, name | All versions |

## circuits.virtualcircuit

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| circuits_virtualcircuit_unique_provider_network_cid | builtin | provider_network, cid | N/A | Matches on unique constraint fields: provider_network, cid | All versions |
| circuits_virtualcircuit_unique_provideraccount_cid | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | All versions |

## circuits.virtualcircuittermination

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_interface | builtin | interface | N/A | Matches on unique field(s): interface | All versions |

## circuits.virtualcircuittype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.cabletermination

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_cabletermination_unique_termination | builtin | termination_type, termination_id | N/A | Matches on unique constraint fields: termination_type, termination_id | All versions |

## dcim.consoleport

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_consoleport_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.consoleporttemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_consoleporttemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_consoleporttemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.consoleserverport

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_consoleserverport_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.consoleserverporttemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_consoleserverporttemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_consoleserverporttemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.device

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_asset_tag | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| unique_primary_ip4 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| unique_oob_ip | builtin | oob_ip | N/A | Matches on unique field(s): oob_ip | All versions |
| dcim_device_unique_name_site_tenant | builtin |  | N/A | Custom matcher | All versions |
| dcim_device_unique_name_site | builtin |  | tenant is NULL | Custom matcher | All versions |
| dcim_device_unique_rack_position_face | builtin | rack, position, face | N/A | Matches on unique constraint fields: rack, position, face | All versions |
| dcim_device_unique_virtual_chassis_vc_position | builtin | virtual_chassis, vc_position | N/A | Matches on unique constraint fields: virtual_chassis, vc_position | All versions |

## dcim.devicebay

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_installed_device | builtin | installed_device | N/A | Matches on unique field(s): installed_device | All versions |
| dcim_devicebay_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.devicebaytemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_devicebaytemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |

## dcim.devicerole

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_device_role_name_no_parent | logical | name | parent is NULL | Matches on fields: name where parent is NULL | ≥4.3.0 |
| logical_device_role_slug_no_parent | logical | slug | parent is NULL | Matches on fields: slug where parent is NULL | ≥4.3.0 |
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.devicetype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_devicetype_unique_manufacturer_model | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |
| dcim_devicetype_unique_manufacturer_slug | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.frontport

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_frontport_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |
| dcim_frontport_unique_rear_port_position | builtin | rear_port, rear_port_position | N/A | Matches on unique constraint fields: rear_port, rear_port_position | All versions |

## dcim.frontporttemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_frontporttemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_frontporttemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |
| dcim_frontporttemplate_unique_rear_port_position | builtin | rear_port, rear_port_position | N/A | Matches on unique constraint fields: rear_port, rear_port_position | All versions |

## dcim.interface

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_primary_mac_address | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | All versions |
| dcim_interface_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.interfacetemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_interfacetemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_interfacetemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.inventoryitem

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_inventory_item_name_on_device_no_parent | logical | name, device | parent is NULL | Matches on fields: name, device where parent is NULL | All versions |
| unique_asset_tag | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| dcim_inventoryitem_unique_device_parent_name | builtin | device, parent, name | N/A | Matches on unique constraint fields: device, parent, name | All versions |

## dcim.inventoryitemrole

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.inventoryitemtemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_inventoryitemtemplate_unique_device_type_parent_name | builtin | device_type, parent, name | N/A | Matches on unique constraint fields: device_type, parent, name | All versions |

## dcim.location

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_location_parent_name | builtin | site, parent, name | N/A | Matches on unique constraint fields: site, parent, name | All versions |
| dcim_location_name | builtin | site, name | parent is NULL | Matches on unique constraint fields: site, name where parent is NULL | All versions |
| dcim_location_parent_slug | builtin | site, parent, slug | N/A | Matches on unique constraint fields: site, parent, slug | All versions |
| dcim_location_slug | builtin | site, slug | parent is NULL | Matches on unique constraint fields: site, slug where parent is NULL | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.macaddress

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_mac_address_within_parent | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NOT NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NOT NULL | All versions |
| logical_mac_address_within_parent | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NULL | All versions |

## dcim.manufacturer

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.module

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_module_bay | builtin | module_bay | N/A | Matches on unique field(s): module_bay | All versions |
| unique_asset_tag | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |

## dcim.modulebay

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_module_bay_name_on_device | logical | name, device | N/A | Matches on fields: name, device | All versions |
| dcim_modulebay_unique_device_module_name | builtin | device, module, name | N/A | Matches on unique constraint fields: device, module, name | All versions |

## dcim.modulebaytemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_modulebaytemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_modulebaytemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.moduletype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_moduletype_unique_manufacturer_model | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |

## dcim.platform

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.powerfeed

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_powerfeed_unique_power_panel_name | builtin | power_panel, name | N/A | Matches on unique constraint fields: power_panel, name | All versions |

## dcim.poweroutlet

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_poweroutlet_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.poweroutlettemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_poweroutlettemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_poweroutlettemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.powerpanel

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_powerpanel_unique_site_name | builtin | site, name | N/A | Matches on unique constraint fields: site, name | All versions |

## dcim.powerport

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_powerport_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.powerporttemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_powerporttemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_powerporttemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.rack

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_asset_tag | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| dcim_rack_unique_location_name | builtin | location, name | N/A | Matches on unique constraint fields: location, name | All versions |
| dcim_rack_unique_location_facility_id | builtin | location, facility_id | N/A | Matches on unique constraint fields: location, facility_id | All versions |

## dcim.rackrole

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.racktype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| dcim_racktype_unique_manufacturer_model | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |
| dcim_racktype_unique_manufacturer_slug | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.rearport

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_rearport_unique_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.rearporttemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_rearporttemplate_unique_device_type_name | builtin | device_type, name | N/A | Matches on unique constraint fields: device_type, name | All versions |
| dcim_rearporttemplate_unique_module_type_name | builtin | module_type, name | N/A | Matches on unique constraint fields: module_type, name | All versions |

## dcim.region

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_region_parent_name | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| dcim_region_name | builtin | name | parent is NULL | Matches on unique constraint fields: name where parent is NULL | All versions |
| dcim_region_parent_slug | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | All versions |
| dcim_region_slug | builtin | slug | parent is NULL | Matches on unique constraint fields: slug where parent is NULL | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.site

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.sitegroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| dcim_sitegroup_parent_name | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| dcim_sitegroup_name | builtin | name | parent is NULL | Matches on unique constraint fields: name where parent is NULL | All versions |
| dcim_sitegroup_parent_slug | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | All versions |
| dcim_sitegroup_slug | builtin | slug | parent is NULL | Matches on unique constraint fields: slug where parent is NULL | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.virtualchassis

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_master | builtin | master | N/A | Matches on unique field(s): master | All versions |

## dcim.virtualdevicecontext

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_primary_ip4 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| dcim_virtualdevicecontext_device_identifier | builtin | device, identifier | N/A | Matches on unique constraint fields: device, identifier | All versions |
| dcim_virtualdevicecontext_device_name | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## extras.bookmark

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| extras_bookmark_unique_per_object_and_user | builtin | object_type, object_id, user | N/A | Matches on unique constraint fields: object_type, object_id, user | All versions |

## extras.configcontext

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.customfield

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.customfieldchoiceset

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.customlink

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.eventrule

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.notificationgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.savedfilter

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## extras.script

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| extras_script_unique_name_module | builtin | name, module | N/A | Matches on unique constraint fields: name, module | All versions |

## extras.tag

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## extras.webhook

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.aggregate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_aggregate_prefix_no_rir | logical | prefix | rir is NULL | Matches on fields: prefix where rir is NULL | All versions |
| logical_aggregate_prefix_within_rir | logical | prefix, rir | rir is NOT NULL | Matches on fields: prefix, rir where rir is NOT NULL | All versions |

## ipam.asn

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_asn | builtin | asn | N/A | Matches on unique field(s): asn | All versions |

## ipam.asnrange

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.fhrpgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_fhrp_group_id | logical | group_id | N/A | Matches on fields: group_id | All versions |

## ipam.fhrpgroupassignment

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| ipam_fhrpgroupassignment_unique_interface_group | builtin | interface_type, interface_id, group | N/A | Matches on unique constraint fields: interface_type, interface_id, group | All versions |

## ipam.ipaddress

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_ip_address_global_no_vrf | logical |  | N/A | Matches IP address address in global namespace (no VRF) | All versions |
| logical_ip_address_within_vrf | logical |  | N/A | Matches IP address address within VRF | All versions |

## ipam.iprange

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_ip_range_start_end_global_no_vrf | logical |  | N/A | Matches IP range start_address, end_address within VRF context | All versions |
| logical_ip_range_start_end_within_vrf | logical |  | N/A | Matches IP range start_address, end_address within VRF context | All versions |

## ipam.prefix

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_prefix_global_no_vrf | logical | prefix | vrf is NULL | Matches on fields: prefix where vrf is NULL | All versions |
| logical_prefix_within_vrf | logical | prefix, vrf | vrf is NOT NULL | Matches on fields: prefix, vrf where vrf is NOT NULL | All versions |

## ipam.rir

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.role

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.routetarget

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.service

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_service_name_no_device_or_vm | logical | name | device is NULL AND virtual_machine is NULL | Matches on fields: name where device is NULL AND virtual_machine is NULL | ≤4.2.99 |
| logical_service_name_on_device | logical | name, device | device is NOT NULL | Matches on fields: name, device where device is NOT NULL | ≤4.2.99 |
| logical_service_name_on_vm | logical | name, virtual_machine | virtual_machine is NOT NULL | Matches on fields: name, virtual_machine where virtual_machine is NOT NULL | ≤4.2.99 |
| logical_service_name_on_parent | logical | name, parent_object_type, parent_object_id | parent_object_type is NOT NULL | Matches on fields: name, parent_object_type, parent_object_id where parent_object_type is NOT NULL | ≥4.3.0 |

## ipam.servicetemplate

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.vlan

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_vlan_vid_no_group_or_svlan | logical | vid | group is NULL AND qinq_svlan is NULL | Matches on fields: vid where group is NULL AND qinq_svlan is NULL | All versions |
| ipam_vlan_unique_group_vid | builtin | group, vid | N/A | Matches on unique constraint fields: group, vid | All versions |
| ipam_vlan_unique_group_name | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| ipam_vlan_unique_qinq_svlan_vid | builtin | qinq_svlan, vid | N/A | Matches on unique constraint fields: qinq_svlan, vid | All versions |
| ipam_vlan_unique_qinq_svlan_name | builtin | qinq_svlan, name | N/A | Matches on unique constraint fields: qinq_svlan, name | All versions |

## ipam.vlangroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_vlan_group_name_no_scope | logical | name | scope_type is NULL | Matches on fields: name where scope_type is NULL | All versions |
| ipam_vlangroup_unique_scope_name | builtin | scope_type, scope_id, name | N/A | Matches on unique constraint fields: scope_type, scope_id, name | All versions |
| ipam_vlangroup_unique_scope_slug | builtin | scope_type, scope_id, slug | N/A | Matches on unique constraint fields: scope_type, scope_id, slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.vlantranslationpolicy

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.vlantranslationrule

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| ipam_vlantranslationrule_unique_policy_local_vid | builtin | policy, local_vid | N/A | Matches on unique constraint fields: policy, local_vid | All versions |
| ipam_vlantranslationrule_unique_policy_remote_vid | builtin | policy, remote_vid | N/A | Matches on unique constraint fields: policy, remote_vid | All versions |

## ipam.vrf

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_rd | builtin | rd | N/A | Matches on unique field(s): rd | All versions |

## tenancy.contact

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_contact_name | logical | name | N/A | Matches on fields: name | ≥4.3.0 |
| tenancy_contact_unique_group_name | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |

## tenancy.contactassignment

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| tenancy_contactassignment_unique_object_contact_role | builtin | object_type, object_id, contact, role | N/A | Matches on unique constraint fields: object_type, object_id, contact, role | All versions |

## tenancy.contactgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| tenancy_contactgroup_unique_parent_name | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.contactrole

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.tenant

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| tenancy_tenant_unique_group_name | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| tenancy_tenant_unique_name | builtin | name | group is NULL | Matches on unique constraint fields: name where group is NULL | All versions |
| tenancy_tenant_unique_group_slug | builtin | group, slug | N/A | Matches on unique constraint fields: group, slug | All versions |
| tenancy_tenant_unique_slug | builtin | slug | group is NULL | Matches on unique constraint fields: slug where group is NULL | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.tenantgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## virtualization.cluster

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_cluster_within_scope | logical | name, scope_type, scope_id | scope_type is NOT NULL | Matches on fields: name, scope_type, scope_id where scope_type is NOT NULL | All versions |
| logical_cluster_with_no_scope_or_group | logical | name | group is NULL AND scope_type is NULL | Matches on fields: name where group is NULL AND scope_type is NULL | All versions |
| virtualization_cluster_unique_group_name | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| virtualization_cluster_unique__site_name | builtin | _site, name | N/A | Matches on unique constraint fields: _site, name | All versions |

## virtualization.clustergroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## virtualization.clustertype

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## virtualization.virtualdisk

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| virtualization_virtualdisk_unique_virtual_machine_name | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | All versions |

## virtualization.virtualmachine

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_virtual_machine_name_no_cluster | logical | name | cluster is NULL | Matches on fields: name where cluster is NULL | All versions |
| unique_primary_ip4 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| virtualization_virtualmachine_unique_name_cluster_tenant | builtin |  | N/A | Custom matcher | All versions |
| virtualization_virtualmachine_unique_name_cluster | builtin |  | tenant is NULL | Custom matcher | All versions |

## virtualization.vminterface

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_primary_mac_address | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | All versions |
| virtualization_vminterface_unique_virtual_machine_name | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | All versions |

## vpn.ikepolicy

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ikeproposal

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecpolicy

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecprofile

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecproposal

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.l2vpn

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## vpn.l2vpntermination

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| vpn_l2vpntermination_assigned_object | builtin | assigned_object_type, assigned_object_id | N/A | Matches on unique constraint fields: assigned_object_type, assigned_object_id | All versions |

## vpn.tunnel

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| vpn_tunnel_group_name | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| vpn_tunnel_name | builtin | name | group is NULL | Matches on unique constraint fields: name where group is NULL | All versions |

## vpn.tunnelgroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## vpn.tunneltermination

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| vpn_tunneltermination_termination | builtin | termination_type, termination_id | N/A | Matches on unique constraint fields: termination_type, termination_id | All versions |

## wireless.wirelesslan

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| logical_wireless_lan_ssid_no_group_or_vlan | logical | ssid | group is NULL AND vlan is NULL | Matches on fields: ssid where group is NULL AND vlan is NULL | All versions |
| logical_wireless_lan_ssid_in_group | logical | ssid, group | group is NOT NULL | Matches on fields: ssid, group where group is NOT NULL | All versions |
| logical_wireless_lan_ssid_in_vlan | logical | ssid, vlan | vlan is NOT NULL | Matches on fields: ssid, vlan where vlan is NOT NULL | All versions |

## wireless.wirelesslangroup

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| unique_name | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| wireless_wirelesslangroup_unique_parent_name | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| unique_autoslug_slug | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## wireless.wirelesslink

| Matcher Name | Type | Fields | Condition | Description | Version Constraints |
|--------------|------|--------|-----------|-------------|-------------------|
| wireless_wirelesslink_unique_interfaces | builtin | interface_a, interface_b | N/A | Matches on unique constraint fields: interface_a, interface_b | All versions |

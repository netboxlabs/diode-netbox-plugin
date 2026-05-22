# NetBox Diode Plugin - Object Matching Criteria

This document describes how the Diode NetBox Plugin matches existing objects when applying changes. The matchers will be applied in the order of their precedence, unttil one of them matches.

## Matcher Types

- **Logical Matchers**: Custom matching criteria that represent likely user intent
- **Builtin Matchers**: Automatically generated from NetBox model constraints (unique fields, unique constraints, custom fields, auto-slugs)

## circuits.circuit

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuit_unique_provider_cid | 1 | builtin | provider, cid | N/A | Matches on unique constraint fields: provider, cid | All versions |
| circuits_circuit_unique_provideraccount_cid | 2 | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | All versions |

## circuits.circuitgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.circuitgroupassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuitgroupassignment_unique_member_group | 1 | builtin | member_type, member_id, group | N/A | Matches on unique constraint fields: member_type, member_id, group | All versions |

## circuits.circuittermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuittermination_unique_circuit_term_side | 1 | builtin | circuit, term_side | N/A | Matches on unique constraint fields: circuit, term_side | All versions |

## circuits.circuittype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.provider

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## circuits.provideraccount

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_provideraccount_unique_provider_account | 1 | builtin | provider, account | N/A | Matches on unique constraint fields: provider, account | All versions |
| circuits_provideraccount_unique_provider_name | 2 | builtin | provider, name | name =  | Matches on unique constraint fields: provider, name where name =  | All versions |

## circuits.providernetwork

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_providernetwork_unique_provider_name | 1 | builtin | provider, name | N/A | Matches on unique constraint fields: provider, name | All versions |

## circuits.virtualcircuit

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_virtualcircuit_unique_provider_network_cid | 1 | builtin | provider_network, cid | N/A | Matches on unique constraint fields: provider_network, cid | All versions |
| circuits_virtualcircuit_unique_provideraccount_cid | 2 | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | All versions |

## circuits.virtualcircuittermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_interface | 1 | builtin | interface | N/A | Matches on unique field(s): interface | All versions |

## circuits.virtualcircuittype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.cabletermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_cabletermination_unique_termination | 1 | builtin | termination_type, termination_id | N/A | Matches on unique constraint fields: termination_type, termination_id | All versions |
| dcim_cabletermination_unique_connector | 2 | builtin | cable, cable_end, connector | N/A | Matches on unique constraint fields: cable, cable_end, connector | All versions |

## dcim.consoleport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_consoleport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.consoleserverport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_consoleserverport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.device

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_asset_tag | 1 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| unique_primary_ip4 | 2 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | 3 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| unique_oob_ip | 4 | builtin | oob_ip | N/A | Matches on unique field(s): oob_ip | All versions |
| dcim_device_unique_name_site_tenant | 5 | builtin |  | N/A | Custom matcher | All versions |
| dcim_device_unique_name_site | 6 | builtin |  | tenant is NULL | Custom matcher | All versions |
| dcim_device_unique_rack_position_face | 7 | builtin | rack, position, face | N/A | Matches on unique constraint fields: rack, position, face | All versions |
| dcim_device_unique_virtual_chassis_vc_position | 8 | builtin | virtual_chassis, vc_position | N/A | Matches on unique constraint fields: virtual_chassis, vc_position | All versions |

## dcim.devicebay

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_installed_device | 1 | builtin | installed_device | N/A | Matches on unique field(s): installed_device | All versions |
| dcim_devicebay_unique_device_name | 2 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.devicerole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_device_role_name_no_parent | 1 | logical | name | parent is NULL | Matches on fields: name where parent is NULL | ≥4.3.0 |
| logical_device_role_slug_no_parent | 2 | logical | slug | parent is NULL | Matches on fields: slug where parent is NULL | ≥4.3.0 |
| dcim_devicerole_parent_name | 3 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| dcim_devicerole_name | 4 | builtin | name | parent is NULL | Matches on unique constraint fields: name where parent is NULL | All versions |
| dcim_devicerole_parent_slug | 5 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | All versions |
| dcim_devicerole_slug | 6 | builtin | slug | parent is NULL | Matches on unique constraint fields: slug where parent is NULL | All versions |
| unique_autoslug_slug | 7 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.devicetype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_devicetype_unique_manufacturer_model | 1 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |
| dcim_devicetype_unique_manufacturer_slug | 2 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.frontport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_frontport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.interface

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_mac_address | 1 | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | All versions |
| dcim_interface_unique_device_name | 2 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.inventoryitem

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_inventory_item_name_on_device_no_parent | 1 | logical | name, device | parent is NULL | Matches on fields: name, device where parent is NULL | All versions |
| unique_asset_tag | 2 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| dcim_inventoryitem_unique_device_parent_name | 3 | builtin | device, parent, name | N/A | Matches on unique constraint fields: device, parent, name | All versions |

## dcim.inventoryitemrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.location

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_location_parent_name | 1 | builtin | site, parent, name | N/A | Matches on unique constraint fields: site, parent, name | All versions |
| dcim_location_name | 2 | builtin | site, name | parent is NULL | Matches on unique constraint fields: site, name where parent is NULL | All versions |
| dcim_location_parent_slug | 3 | builtin | site, parent, slug | N/A | Matches on unique constraint fields: site, parent, slug | All versions |
| dcim_location_slug | 4 | builtin | site, slug | parent is NULL | Matches on unique constraint fields: site, slug where parent is NULL | All versions |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.macaddress

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_mac_address_within_parent | 1 | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NOT NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NOT NULL | All versions |
| logical_mac_address_within_parent | 2 | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NULL | All versions |

## dcim.manufacturer

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.module

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_module_bay | 1 | builtin | module_bay | N/A | Matches on unique field(s): module_bay | All versions |
| unique_asset_tag | 2 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |

## dcim.modulebay

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_module_bay_name_on_device | 1 | logical | name, device | N/A | Matches on fields: name, device | All versions |
| dcim_modulebay_unique_device_module_name | 2 | builtin | device, module, name | N/A | Matches on unique constraint fields: device, module, name | All versions |

## dcim.moduletype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_moduletype_unique_manufacturer_model | 1 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |

## dcim.moduletypeprofile

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## dcim.platform

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_platform_manufacturer_name | 1 | builtin | manufacturer, name | N/A | Matches on unique constraint fields: manufacturer, name | All versions |
| dcim_platform_name | 2 | builtin | name | manufacturer is NULL | Matches on unique constraint fields: name where manufacturer is NULL | All versions |
| dcim_platform_manufacturer_slug | 3 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | All versions |
| dcim_platform_slug | 4 | builtin | slug | manufacturer is NULL | Matches on unique constraint fields: slug where manufacturer is NULL | All versions |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.powerfeed

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerfeed_unique_power_panel_name | 1 | builtin | power_panel, name | N/A | Matches on unique constraint fields: power_panel, name | All versions |

## dcim.poweroutlet

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_poweroutlet_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.powerpanel

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerpanel_unique_site_name | 1 | builtin | site, name | N/A | Matches on unique constraint fields: site, name | All versions |

## dcim.powerport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.rack

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_asset_tag | 1 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | All versions |
| dcim_rack_unique_location_name | 2 | builtin | location, name | N/A | Matches on unique constraint fields: location, name | All versions |
| dcim_rack_unique_location_facility_id | 3 | builtin | location, facility_id | N/A | Matches on unique constraint fields: location, facility_id | All versions |

## dcim.rackrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.racktype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_slug | 1 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| dcim_racktype_unique_manufacturer_model | 2 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | All versions |
| dcim_racktype_unique_manufacturer_slug | 3 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | All versions |
| unique_autoslug_slug | 4 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.rearport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_rearport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## dcim.region

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_region_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| dcim_region_name | 2 | builtin | name | parent is NULL | Matches on unique constraint fields: name where parent is NULL | All versions |
| dcim_region_parent_slug | 3 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | All versions |
| dcim_region_slug | 4 | builtin | slug | parent is NULL | Matches on unique constraint fields: slug where parent is NULL | All versions |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.site

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.sitegroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_sitegroup_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| dcim_sitegroup_name | 2 | builtin | name | parent is NULL | Matches on unique constraint fields: name where parent is NULL | All versions |
| dcim_sitegroup_parent_slug | 3 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | All versions |
| dcim_sitegroup_slug | 4 | builtin | slug | parent is NULL | Matches on unique constraint fields: slug where parent is NULL | All versions |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## dcim.virtualchassis

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_master | 1 | builtin | master | N/A | Matches on unique field(s): master | All versions |

## dcim.virtualdevicecontext

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_ip4 | 1 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | 2 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| dcim_virtualdevicecontext_device_identifier | 3 | builtin | device, identifier | N/A | Matches on unique constraint fields: device, identifier | All versions |
| dcim_virtualdevicecontext_device_name | 4 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | All versions |

## extras.customfield

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.customfieldchoiceset

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.customlink

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## extras.journalentry

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_journal_entry_assigned_object_comments | 1 | logical | assigned_object_id, assigned_object_type, comments | N/A | Matches on fields: assigned_object_id, assigned_object_type, comments | All versions |

## extras.tag

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.aggregate

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_aggregate_prefix_no_rir | 1 | logical | prefix | rir is NULL | Matches on fields: prefix where rir is NULL | All versions |
| logical_aggregate_prefix_within_rir | 2 | logical | prefix, rir | rir is NOT NULL | Matches on fields: prefix, rir where rir is NOT NULL | All versions |

## ipam.asn

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_asn | 1 | builtin | asn | N/A | Matches on unique field(s): asn | All versions |

## ipam.asnrange

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.fhrpgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_fhrp_group_id | 1 | logical | group_id | N/A | Matches on fields: group_id | All versions |

## ipam.fhrpgroupassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| ipam_fhrpgroupassignment_unique_interface_group | 1 | builtin | interface_type, interface_id, group | N/A | Matches on unique constraint fields: interface_type, interface_id, group | All versions |

## ipam.ipaddress

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_ip_address_global_no_vrf | 1 | logical |  | N/A | Matches IP address address in global namespace (no VRF) | All versions |
| logical_ip_address_within_vrf | 2 | logical |  | N/A | Matches IP address address within VRF | All versions |

## ipam.iprange

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_ip_range_start_end_global_no_vrf | 1 | logical |  | N/A | Matches IP range start_address, end_address within VRF context | All versions |
| logical_ip_range_start_end_within_vrf | 2 | logical |  | N/A | Matches IP range start_address, end_address within VRF context | All versions |

## ipam.prefix

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_prefix_global_no_vrf | 1 | logical | prefix | vrf is NULL | Matches on fields: prefix where vrf is NULL | All versions |
| logical_prefix_within_vrf | 2 | logical | prefix, vrf | vrf is NOT NULL | Matches on fields: prefix, vrf where vrf is NOT NULL | All versions |

## ipam.rir

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.role

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.routetarget

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.service

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_service_name_no_device_or_vm | 1 | logical | name | device is NULL AND virtual_machine is NULL | Matches on fields: name where device is NULL AND virtual_machine is NULL | ≤4.2.99 |
| logical_service_name_on_device | 2 | logical | name, device | device is NOT NULL | Matches on fields: name, device where device is NOT NULL | ≤4.2.99 |
| logical_service_name_on_vm | 3 | logical | name, virtual_machine | virtual_machine is NOT NULL | Matches on fields: name, virtual_machine where virtual_machine is NOT NULL | ≤4.2.99 |
| logical_service_name_on_parent | 4 | logical | name, parent_object_type, parent_object_id | parent_object_type is NOT NULL | Matches on fields: name, parent_object_type, parent_object_id where parent_object_type is NOT NULL | ≥4.3.0 |

## ipam.vlan

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vlan_vid_no_group_or_svlan_or_site | 1 | logical | vid | group is NULL AND qinq_svlan is NULL AND site is NULL | Matches on fields: vid where group is NULL AND qinq_svlan is NULL AND site is NULL | All versions |
| logical_vlan_in_site | 2 | logical | vid, site | group is NULL AND qinq_svlan is NULL AND site is NOT NULL | Matches on fields: vid, site where group is NULL AND qinq_svlan is NULL AND site is NOT NULL | All versions |
| ipam_vlan_unique_group_vid | 3 | builtin | group, vid | N/A | Matches on unique constraint fields: group, vid | All versions |
| ipam_vlan_unique_group_name | 4 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| ipam_vlan_unique_qinq_svlan_vid | 5 | builtin | qinq_svlan, vid | N/A | Matches on unique constraint fields: qinq_svlan, vid | All versions |
| ipam_vlan_unique_qinq_svlan_name | 6 | builtin | qinq_svlan, name | N/A | Matches on unique constraint fields: qinq_svlan, name | All versions |

## ipam.vlangroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vlan_group_name_no_scope | 1 | logical | name | scope_type is NULL | Matches on fields: name where scope_type is NULL | All versions |
| ipam_vlangroup_unique_scope_name | 2 | builtin | scope_type, scope_id, name | N/A | Matches on unique constraint fields: scope_type, scope_id, name | All versions |
| ipam_vlangroup_unique_scope_slug | 3 | builtin | scope_type, scope_id, slug | N/A | Matches on unique constraint fields: scope_type, scope_id, slug | All versions |
| unique_autoslug_slug | 4 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## ipam.vlantranslationpolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## ipam.vlantranslationrule

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| ipam_vlantranslationrule_unique_policy_local_vid | 1 | builtin | policy, local_vid | N/A | Matches on unique constraint fields: policy, local_vid | All versions |
| ipam_vlantranslationrule_unique_policy_remote_vid | 2 | builtin | policy, remote_vid | N/A | Matches on unique constraint fields: policy, remote_vid | All versions |

## ipam.vrf

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vrf_name_no_tenant | 1 | logical | name | rd is NULL AND tenant is NULL | Matches on fields: name where rd is NULL AND tenant is NULL | All versions |
| logical_vrf_name_within_tenant | 2 | logical | name, tenant | rd is NULL AND tenant is NOT NULL | Matches on fields: name, tenant where rd is NULL AND tenant is NOT NULL | All versions |
| unique_rd | 3 | builtin | rd | N/A | Matches on unique field(s): rd | All versions |

## tenancy.contact

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_contact_name | 1 | logical | name | N/A | Matches on fields: name | ≥4.3.0 |

## tenancy.contactassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_contactassignment_unique_object_contact_role | 1 | builtin | object_type, object_id, contact, role | N/A | Matches on unique constraint fields: object_type, object_id, contact, role | All versions |

## tenancy.contactgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_contactgroup_unique_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| unique_autoslug_slug | 2 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.contactrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.tenant

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_tenant_unique_group_name | 1 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| tenancy_tenant_unique_name | 2 | builtin | name | group is NULL | Matches on unique constraint fields: name where group is NULL | All versions |
| tenancy_tenant_unique_group_slug | 3 | builtin | group, slug | N/A | Matches on unique constraint fields: group, slug | All versions |
| tenancy_tenant_unique_slug | 4 | builtin | slug | group is NULL | Matches on unique constraint fields: slug where group is NULL | All versions |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## tenancy.tenantgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## users.owner

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## users.ownergroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## virtualization.cluster

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_cluster_within_scope | 1 | logical | name, scope_type, scope_id | scope_type is NOT NULL | Matches on fields: name, scope_type, scope_id where scope_type is NOT NULL | All versions |
| logical_cluster_with_no_scope_or_group | 2 | logical | name | group is NULL AND scope_type is NULL | Matches on fields: name where group is NULL AND scope_type is NULL | All versions |
| virtualization_cluster_unique_group_name | 3 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| virtualization_cluster_unique__site_name | 4 | builtin | _site, name | N/A | Matches on unique constraint fields: _site, name | All versions |

## virtualization.clustergroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## virtualization.clustertype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## virtualization.virtualdisk

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| virtualization_virtualdisk_unique_virtual_machine_name | 1 | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | All versions |

## virtualization.virtualmachine

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_virtual_machine_name_no_cluster | 1 | logical | name | cluster is NULL | Matches on fields: name where cluster is NULL | All versions |
| unique_primary_ip4 | 2 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | All versions |
| unique_primary_ip6 | 3 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | All versions |
| virtualization_virtualmachine_unique_name_cluster_tenant | 4 | builtin |  | N/A | Custom matcher | All versions |
| virtualization_virtualmachine_unique_name_cluster | 5 | builtin |  | tenant is NULL | Custom matcher | All versions |
| virtualization_virtualmachine_unique_name_device_tenant | 6 | builtin |  | cluster is NULL AND device is NOT NULL | Custom matcher | All versions |
| virtualization_virtualmachine_unique_name_device | 7 | builtin |  | cluster is NULL AND device is NOT NULL AND tenant is NULL | Custom matcher | All versions |

## virtualization.vminterface

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_mac_address | 1 | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | All versions |
| virtualization_vminterface_unique_virtual_machine_name | 2 | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | All versions |

## vpn.ikepolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ikeproposal

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecpolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecprofile

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.ipsecproposal

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |

## vpn.l2vpn

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## vpn.l2vpntermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| vpn_l2vpntermination_assigned_object | 1 | builtin | assigned_object_type, assigned_object_id | N/A | Matches on unique constraint fields: assigned_object_type, assigned_object_id | All versions |

## vpn.tunnel

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| vpn_tunnel_group_name | 2 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | All versions |
| vpn_tunnel_name | 3 | builtin | name | group is NULL | Matches on unique constraint fields: name where group is NULL | All versions |

## vpn.tunnelgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## vpn.tunneltermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| vpn_tunneltermination_termination | 1 | builtin | termination_type, termination_id | N/A | Matches on unique constraint fields: termination_type, termination_id | All versions |

## wireless.wirelesslan

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_wireless_lan_ssid_no_group_or_vlan | 1 | logical | ssid | group is NULL AND vlan is NULL | Matches on fields: ssid where group is NULL AND vlan is NULL | All versions |
| logical_wireless_lan_ssid_in_group | 2 | logical | ssid, group | group is NOT NULL | Matches on fields: ssid, group where group is NOT NULL | All versions |
| logical_wireless_lan_ssid_in_vlan | 3 | logical | ssid, vlan | vlan is NOT NULL | Matches on fields: ssid, vlan where vlan is NOT NULL | All versions |

## wireless.wirelesslangroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | All versions |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | All versions |
| wireless_wirelesslangroup_unique_parent_name | 3 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | All versions |
| unique_autoslug_slug | 4 | builtin | slug | N/A | Matches on auto-generated slug field: slug | All versions |

## wireless.wirelesslink

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| wireless_wirelesslink_unique_interfaces | 1 | builtin | interface_a, interface_b | N/A | Matches on unique constraint fields: interface_a, interface_b | All versions |

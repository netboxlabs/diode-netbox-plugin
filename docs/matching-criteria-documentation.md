# NetBox Diode Plugin - Object Matching Criteria

This document describes how the Diode NetBox Plugin matches existing objects when applying changes. The matchers will be applied in the order of their precedence, unttil one of them matches.

Generated on NetBox 4.7.0.

Builtin matchers are derived from that release's model constraints and are listed as they exist there. Other NetBox releases the plugin supports may declare the same identity through different constraints (for example, NetBox 4.7 replaced conditional constraint pairs such as `name where parent is NULL` with single `nulls_distinct=False` constraints); the matcher derives whichever form the running release declares. In the Version Constraints column, "NetBox <version>" on a builtin row means the row reflects that release; a version range on a logical row is the plugin's own gate.

## Matcher Types

- **Logical Matchers**: Custom matching criteria that represent likely user intent
- **Builtin Matchers**: Automatically generated from NetBox model constraints (unique fields, unique constraints, custom fields, auto-slugs)

## circuits.circuit

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuit_unique_provider_cid | 1 | builtin | provider, cid | N/A | Matches on unique constraint fields: provider, cid | NetBox 4.7.0 |
| circuits_circuit_unique_provideraccount_cid | 2 | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | NetBox 4.7.0 |

## circuits.circuitgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## circuits.circuitgroupassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuitgroupassignment_unique_member_group | 1 | builtin | member_type, member_id, group | N/A | Matches on unique constraint fields: member_type, member_id, group | NetBox 4.7.0 |

## circuits.circuittermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_circuittermination_unique_circuit_term_side | 1 | builtin | circuit, term_side | N/A | Matches on unique constraint fields: circuit, term_side | NetBox 4.7.0 |

## circuits.circuittype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## circuits.provider

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## circuits.provideraccount

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_provideraccount_unique_provider_account | 1 | builtin | provider, account | N/A | Matches on unique constraint fields: provider, account | NetBox 4.7.0 |
| circuits_provideraccount_unique_provider_name | 2 | builtin | provider, name | name =  | Matches on unique constraint fields: provider, name where name =  | NetBox 4.7.0 |

## circuits.providernetwork

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_providernetwork_unique_provider_name | 1 | builtin | provider, name | N/A | Matches on unique constraint fields: provider, name | NetBox 4.7.0 |

## circuits.virtualcircuit

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| circuits_virtualcircuit_unique_provider_network_cid | 1 | builtin | provider_network, cid | N/A | Matches on unique constraint fields: provider_network, cid | NetBox 4.7.0 |
| circuits_virtualcircuit_unique_provideraccount_cid | 2 | builtin | provider_account, cid | N/A | Matches on unique constraint fields: provider_account, cid | NetBox 4.7.0 |

## circuits.virtualcircuittermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_interface | 1 | builtin | interface | N/A | Matches on unique field(s): interface | NetBox 4.7.0 |

## circuits.virtualcircuittype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## core.managedfile

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| core_managedfile_unique_root_path | 1 | builtin | file_root, file_path | N/A | Matches on unique constraint fields: file_root, file_path | NetBox 4.7.0 |

## dcim.cable

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_cable_termination_set | 1 | logical | a_terminations, b_terminations | N/A | Match a Cable by its canonical set of terminations. | All versions |

## dcim.cablebundle

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## dcim.consoleport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_consoleport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.consoleserverport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_consoleserverport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.coolingfeed

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_coolingfeed_unique_cooling_source_name | 1 | builtin | cooling_source, name | N/A | Matches on unique constraint fields: cooling_source, name | NetBox 4.7.0 |

## dcim.coolingintake

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_coolingintake_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.coolingoutflow

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_coolingoutflow_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.coolingsource

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_coolingsource_unique_site_name | 1 | builtin | site, name | N/A | Matches on unique constraint fields: site, name | NetBox 4.7.0 |

## dcim.device

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_asset_tag | 1 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | NetBox 4.7.0 |
| unique_primary_ip4 | 2 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | NetBox 4.7.0 |
| unique_primary_ip6 | 3 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | NetBox 4.7.0 |
| unique_oob_ip | 4 | builtin | oob_ip | N/A | Matches on unique field(s): oob_ip | NetBox 4.7.0 |
| dcim_device_unique_name_site_tenant | 5 | builtin |  | name is NOT NULL | Custom matcher | NetBox 4.7.0 |
| dcim_device_unique_rack_position_face | 6 | builtin | rack, position, face | N/A | Matches on unique constraint fields: rack, position, face | NetBox 4.7.0 |
| dcim_device_unique_virtual_chassis_vc_position | 7 | builtin | virtual_chassis, vc_position | N/A | Matches on unique constraint fields: virtual_chassis, vc_position | NetBox 4.7.0 |

## dcim.devicebay

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_installed_device | 1 | builtin | installed_device | N/A | Matches on unique field(s): installed_device | NetBox 4.7.0 |
| dcim_devicebay_unique_device_name | 2 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.devicerole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_device_role_name_no_parent | 1 | logical | name | parent is NULL | Matches on fields: name where parent is NULL | ≥4.3.0 |
| logical_device_role_slug_no_parent | 2 | logical | slug | parent is NULL | Matches on fields: slug where parent is NULL | ≥4.3.0 |
| dcim_devicerole_parent_name | 3 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | NetBox 4.7.0 |
| dcim_devicerole_parent_slug | 4 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 5 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.devicetype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_devicetype_unique_manufacturer_model | 1 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | NetBox 4.7.0 |
| dcim_devicetype_unique_manufacturer_slug | 2 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.frontport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_frontport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.interface

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_mac_address | 1 | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | NetBox 4.7.0 |
| dcim_interface_unique_device_name | 2 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |
| dcim_interface_unique_parent_channel_id | 3 | builtin | parent, channel_id | N/A | Matches on unique constraint fields: parent, channel_id | NetBox 4.7.0 |

## dcim.inventoryitem

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_inventory_item_name_on_device_no_parent | 1 | logical | name, device | parent is NULL | Matches on fields: name, device where parent is NULL | All versions |
| unique_asset_tag | 2 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | NetBox 4.7.0 |
| dcim_inventoryitem_unique_device_parent_name | 3 | builtin | device, parent, name | N/A | Matches on unique constraint fields: device, parent, name | NetBox 4.7.0 |

## dcim.inventoryitemrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.location

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_location_parent_name | 1 | builtin | site, parent, name | N/A | Matches on unique constraint fields: site, parent, name | NetBox 4.7.0 |
| dcim_location_parent_slug | 2 | builtin | site, parent, slug | N/A | Matches on unique constraint fields: site, parent, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.macaddress

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_mac_address_within_parent | 1 | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NOT NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NOT NULL | All versions |
| logical_mac_address_within_parent | 2 | logical | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NULL | All versions |

## dcim.manufacturer

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.module

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_module_bay | 1 | builtin | module_bay | N/A | Matches on unique field(s): module_bay | NetBox 4.7.0 |
| unique_asset_tag | 2 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | NetBox 4.7.0 |

## dcim.modulebay

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_module_bay_name_on_device | 1 | logical | name, device | N/A | Matches on fields: name, device | All versions |
| dcim_modulebay_unique_device_module_name | 2 | builtin | device, module, name | N/A | Matches on unique constraint fields: device, module, name | NetBox 4.7.0 |

## dcim.modulebaytype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_modulebaytype_unique_manufacturer_name | 1 | builtin | manufacturer, name | N/A | Matches on unique constraint fields: manufacturer, name | NetBox 4.7.0 |
| dcim_modulebaytype_unique_manufacturer_slug | 2 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.moduletype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_moduletype_unique_manufacturer_model | 1 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | NetBox 4.7.0 |

## dcim.moduletypeprofile

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## dcim.platform

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_platform_manufacturer_name | 1 | builtin | manufacturer, name | N/A | Matches on unique constraint fields: manufacturer, name | NetBox 4.7.0 |
| dcim_platform_manufacturer_slug | 2 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.powerfeed

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerfeed_unique_power_panel_name | 1 | builtin | power_panel, name | N/A | Matches on unique constraint fields: power_panel, name | NetBox 4.7.0 |

## dcim.poweroutlet

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_poweroutlet_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.powerpanel

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerpanel_unique_site_name | 1 | builtin | site, name | N/A | Matches on unique constraint fields: site, name | NetBox 4.7.0 |

## dcim.powerport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_powerport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.rack

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_rack_site_name_no_location | 1 | logical | site, name | N/A | Match a location-less rack payload by (site, name), any location. | All versions |
| unique_asset_tag | 2 | builtin | asset_tag | N/A | Matches on unique field(s): asset_tag | NetBox 4.7.0 |
| dcim_rack_unique_location_name | 3 | builtin | location, name | N/A | Matches on unique constraint fields: location, name | NetBox 4.7.0 |
| dcim_rack_unique_location_facility_id | 4 | builtin | location, facility_id | N/A | Matches on unique constraint fields: location, facility_id | NetBox 4.7.0 |

## dcim.rackgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.rackreservation

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_rackreservation_unit_overlap | 1 | logical | rack, units | N/A | Match a RackReservation by unit overlap within its rack. | All versions |

## dcim.rackrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.racktype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_slug | 1 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| dcim_racktype_unique_manufacturer_model | 2 | builtin | manufacturer, model | N/A | Matches on unique constraint fields: manufacturer, model | NetBox 4.7.0 |
| dcim_racktype_unique_manufacturer_slug | 3 | builtin | manufacturer, slug | N/A | Matches on unique constraint fields: manufacturer, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 4 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.rearport

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_rearport_unique_device_name | 1 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## dcim.region

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_region_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | NetBox 4.7.0 |
| dcim_region_parent_slug | 2 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.site

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.sitegroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| dcim_sitegroup_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | NetBox 4.7.0 |
| dcim_sitegroup_parent_slug | 2 | builtin | parent, slug | N/A | Matches on unique constraint fields: parent, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## dcim.virtualchassis

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vc_name_no_master | 1 | logical | name | N/A | Best-effort VirtualChassis matcher: by name, only when the payload has no master. | All versions |
| unique_master | 2 | builtin | master | N/A | Matches on unique field(s): master | NetBox 4.7.0 |

## dcim.virtualdevicecontext

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_ip4 | 1 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | NetBox 4.7.0 |
| unique_primary_ip6 | 2 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | NetBox 4.7.0 |
| dcim_virtualdevicecontext_device_identifier | 3 | builtin | device, identifier | N/A | Matches on unique constraint fields: device, identifier | NetBox 4.7.0 |
| dcim_virtualdevicecontext_device_name | 4 | builtin | device, name | N/A | Matches on unique constraint fields: device, name | NetBox 4.7.0 |

## extras.customfield

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## extras.customfieldchoiceset

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## extras.customlink

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## extras.journalentry

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_journal_entry_assigned_object_comments | 1 | logical | assigned_object_id, assigned_object_type, comments | N/A | Matches on fields: assigned_object_id, assigned_object_type, comments | All versions |

## extras.tag

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## ipam.aggregate

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_aggregate_prefix_no_rir | 1 | logical | prefix | rir is NULL | Matches on fields: prefix where rir is NULL | All versions |
| logical_aggregate_prefix_within_rir | 2 | logical | prefix, rir | rir is NOT NULL | Matches on fields: prefix, rir where rir is NOT NULL | All versions |

## ipam.asn

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_asn | 1 | builtin | asn | N/A | Matches on unique field(s): asn | NetBox 4.7.0 |

## ipam.asnrange

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## ipam.fhrpgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_fhrp_group_id | 1 | logical | group_id | N/A | Matches on fields: group_id | All versions |

## ipam.fhrpgroupassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| ipam_fhrpgroupassignment_unique_interface_group | 1 | builtin | interface_type, interface_id, group | N/A | Matches on unique constraint fields: interface_type, interface_id, group | NetBox 4.7.0 |

## ipam.ipaddress

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_ip_address_global_no_vrf | 1 | logical | address | N/A | Matches IP address address in global namespace (no VRF) | All versions |
| logical_ip_address_within_vrf | 2 | logical | address | N/A | Matches IP address address within VRF | All versions |

## ipam.iprange

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_ip_range_start_end_global_no_vrf | 1 | logical | start_address, end_address | N/A | Matches IP range start_address, end_address in global namespace (no VRF) | All versions |
| logical_ip_range_start_end_within_vrf | 2 | logical | start_address, end_address | N/A | Matches IP range start_address, end_address within VRF context | All versions |

## ipam.prefix

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_prefix_global_no_vrf | 1 | logical | prefix | vrf is NULL | Matches on fields: prefix where vrf is NULL | All versions |
| logical_prefix_within_vrf | 2 | logical | prefix, vrf | vrf is NOT NULL | Matches on fields: prefix, vrf where vrf is NOT NULL | All versions |

## ipam.rir

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## ipam.role

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## ipam.routetarget

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

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
| ipam_vlan_unique_group_vid | 3 | builtin | group, vid | N/A | Matches on unique constraint fields: group, vid | NetBox 4.7.0 |
| ipam_vlan_unique_group_name | 4 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | NetBox 4.7.0 |
| ipam_vlan_unique_qinq_svlan_vid | 5 | builtin | qinq_svlan, vid | N/A | Matches on unique constraint fields: qinq_svlan, vid | NetBox 4.7.0 |
| ipam_vlan_unique_qinq_svlan_name | 6 | builtin | qinq_svlan, name | N/A | Matches on unique constraint fields: qinq_svlan, name | NetBox 4.7.0 |

## ipam.vlangroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vlan_group_name_no_scope | 1 | logical | name | scope_type is NULL | Matches on fields: name where scope_type is NULL | All versions |
| ipam_vlangroup_unique_scope_name | 2 | builtin | scope_type, scope_id, name | N/A | Matches on unique constraint fields: scope_type, scope_id, name | NetBox 4.7.0 |
| ipam_vlangroup_unique_scope_slug | 3 | builtin | scope_type, scope_id, slug | N/A | Matches on unique constraint fields: scope_type, scope_id, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 4 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## ipam.vlantranslationpolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## ipam.vlantranslationrule

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| ipam_vlantranslationrule_unique_policy_local_vid | 1 | builtin | policy, local_vid | N/A | Matches on unique constraint fields: policy, local_vid | NetBox 4.7.0 |
| ipam_vlantranslationrule_unique_policy_remote_vid | 2 | builtin | policy, remote_vid | N/A | Matches on unique constraint fields: policy, remote_vid | NetBox 4.7.0 |

## ipam.vrf

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_vrf_name_no_tenant | 1 | logical | name | rd is NULL AND tenant is NULL | Matches on fields: name where rd is NULL AND tenant is NULL | All versions |
| logical_vrf_name_within_tenant | 2 | logical | name, tenant | rd is NULL AND tenant is NOT NULL | Matches on fields: name, tenant where rd is NULL AND tenant is NOT NULL | All versions |
| unique_rd | 3 | builtin | rd | N/A | Matches on unique field(s): rd | NetBox 4.7.0 |

## tenancy.contact

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_contact_name | 1 | logical | name | N/A | Matches on fields: name | ≥4.3.0 |

## tenancy.contactassignment

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_contactassignment_unique_object_contact_role | 1 | builtin | object_type, object_id, contact, role | N/A | Matches on unique constraint fields: object_type, object_id, contact, role | NetBox 4.7.0 |

## tenancy.contactgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_contactgroup_unique_parent_name | 1 | builtin | parent, name | N/A | Matches on unique constraint fields: parent, name | NetBox 4.7.0 |
| unique_autoslug_slug | 2 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## tenancy.contactrole

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## tenancy.tenant

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| tenancy_tenant_unique_group_name | 1 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | NetBox 4.7.0 |
| tenancy_tenant_unique_group_slug | 2 | builtin | group, slug | N/A | Matches on unique constraint fields: group, slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## tenancy.tenantgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## users.owner

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## users.ownergroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## users.user

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_username | 1 | builtin | username | N/A | Matches on unique field(s): username | NetBox 4.7.0 |

## virtualization.cluster

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_cluster_within_scope | 1 | logical | name, scope_type, scope_id | scope_type is NOT NULL | Matches on fields: name, scope_type, scope_id where scope_type is NOT NULL | All versions |
| logical_cluster_with_no_scope_or_group | 2 | logical | name | group is NULL AND scope_type is NULL | Matches on fields: name where group is NULL AND scope_type is NULL | All versions |
| virtualization_cluster_unique_group_name | 3 | builtin | group, name | N/A | Matches on unique constraint fields: group, name | NetBox 4.7.0 |
| virtualization_cluster_unique__site_name | 4 | builtin | _site, name | N/A | Matches on unique constraint fields: _site, name | NetBox 4.7.0 |

## virtualization.clustergroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## virtualization.clustertype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## virtualization.virtualdisk

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| virtualization_virtualdisk_unique_virtual_machine_name | 1 | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | NetBox 4.7.0 |

## virtualization.virtualmachine

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_virtual_machine_name_no_cluster | 1 | logical | name | cluster is NULL | Matches on fields: name where cluster is NULL | All versions |
| unique_primary_ip4 | 2 | builtin | primary_ip4 | N/A | Matches on unique field(s): primary_ip4 | NetBox 4.7.0 |
| unique_primary_ip6 | 3 | builtin | primary_ip6 | N/A | Matches on unique field(s): primary_ip6 | NetBox 4.7.0 |
| virtualization_virtualmachine_unique_name_cluster_tenant | 4 | builtin |  | cluster is NOT NULL | Custom matcher | NetBox 4.7.0 |
| virtualization_virtualmachine_unique_name_device_tenant | 5 | builtin |  | cluster is NULL AND device is NOT NULL | Custom matcher | NetBox 4.7.0 |

## virtualization.virtualmachinetype

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_slug | 1 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| virtualization_virtualmachinetype_unique_name | 2 | builtin |  | N/A | Custom matcher | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## virtualization.vminterface

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_primary_mac_address | 1 | builtin | primary_mac_address | N/A | Matches on unique field(s): primary_mac_address | NetBox 4.7.0 |
| virtualization_vminterface_unique_virtual_machine_name | 2 | builtin | virtual_machine, name | N/A | Matches on unique constraint fields: virtual_machine, name | NetBox 4.7.0 |

## vpn.ikepolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.ikeproposal

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.ipsecpolicy

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.ipsecprofile

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.ipsecproposal

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.l2vpn

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## vpn.l2vpntermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| vpn_l2vpntermination_assigned_object | 1 | builtin | assigned_object_type, assigned_object_id | N/A | Matches on unique constraint fields: assigned_object_type, assigned_object_id | NetBox 4.7.0 |

## vpn.tunnel

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |

## vpn.tunnelgroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## vpn.tunneltermination

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| vpn_tunneltermination_termination | 1 | builtin | termination_type, termination_id | N/A | Matches on unique constraint fields: termination_type, termination_id | NetBox 4.7.0 |

## wireless.wirelesslan

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| logical_wireless_lan_ssid_no_group_or_vlan | 1 | logical | ssid | group is NULL AND vlan is NULL | Matches on fields: ssid where group is NULL AND vlan is NULL | All versions |
| logical_wireless_lan_ssid_in_group | 2 | logical | ssid, group | group is NOT NULL | Matches on fields: ssid, group where group is NOT NULL | All versions |
| logical_wireless_lan_ssid_in_vlan | 3 | logical | ssid, vlan | vlan is NOT NULL | Matches on fields: ssid, vlan where vlan is NOT NULL | All versions |

## wireless.wirelesslangroup

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| unique_name | 1 | builtin | name | N/A | Matches on unique field(s): name | NetBox 4.7.0 |
| unique_slug | 2 | builtin | slug | N/A | Matches on unique field(s): slug | NetBox 4.7.0 |
| unique_autoslug_slug | 3 | builtin | slug | N/A | Matches on auto-generated slug field: slug | NetBox 4.7.0 |

## wireless.wirelesslink

| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |
|--------------|---------------------|------|--------|-----------|-------------|---------------------|
| wireless_wirelesslink_unique_interfaces | 1 | builtin | interface_a, interface_b | N/A | Matches on unique constraint fields: interface_a, interface_b | NetBox 4.7.0 |

🧬 loaded config '/etc/netbox/config/configuration.py'
🧬 loaded config '/etc/netbox/config/extra.py'
🧬 loaded config '/etc/netbox/config/logging.py'
🧬 loaded config '/etc/netbox/config/plugins.py'
Analyzing matching criteria...
Generating markdown documentation...
# NetBox Diode Plugin - Object Matching Criteria

This document describes how the Diode NetBox Plugin matches existing objects when applying changes.

## dcim.devicerole

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_device_role_name_no_parent | name | parent is NULL | Matches on fields: name where parent is NULL | ≥4.3.0 |
| logical_device_role_slug_no_parent | slug | parent is NULL | Matches on fields: slug where parent is NULL | ≥4.3.0 |

## dcim.inventoryitem

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_inventory_item_name_on_device_no_parent | name, device | parent is NULL | Matches on fields: name, device where parent is NULL | All versions |

## dcim.macaddress

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_mac_address_within_parent | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NOT NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NOT NULL | All versions |
| logical_mac_address_within_parent | mac_address, assigned_object_type, assigned_object_id | assigned_object_id is NULL | Matches on fields: mac_address, assigned_object_type, assigned_object_id where assigned_object_id is NULL | All versions |

## dcim.modulebay

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_module_bay_name_on_device | name, device | N/A | Matches on fields: name, device | All versions |

## ipam.aggregate

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_aggregate_prefix_no_rir | prefix | rir is NULL | Matches on fields: prefix where rir is NULL | All versions |
| logical_aggregate_prefix_within_rir | prefix, rir | rir is NOT NULL | Matches on fields: prefix, rir where rir is NOT NULL | All versions |

## ipam.fhrpgroup

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_fhrp_group_id | group_id | N/A | Matches on fields: group_id | All versions |

## ipam.ipaddress

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_ip_address_global_no_vrf | N/A | N/A | Matches IP address address in global namespace (no VRF) | All versions |
| logical_ip_address_within_vrf | N/A | N/A | Matches IP address address within VRF | All versions |

## ipam.iprange

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_ip_range_start_end_global_no_vrf | N/A | N/A | Matches IP range start_address, end_address within VRF context | All versions |
| logical_ip_range_start_end_within_vrf | N/A | N/A | Matches IP range start_address, end_address within VRF context | All versions |

## ipam.prefix

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_prefix_global_no_vrf | prefix | vrf is NULL | Matches on fields: prefix where vrf is NULL | All versions |
| logical_prefix_within_vrf | prefix, vrf | vrf is NOT NULL | Matches on fields: prefix, vrf where vrf is NOT NULL | All versions |

## ipam.service

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_service_name_no_device_or_vm | name | device is NULL AND virtual_machine is NULL | Matches on fields: name where device is NULL AND virtual_machine is NULL | ≤4.2.99 |
| logical_service_name_on_device | name, device | device is NOT NULL | Matches on fields: name, device where device is NOT NULL | ≤4.2.99 |
| logical_service_name_on_vm | name, virtual_machine | virtual_machine is NOT NULL | Matches on fields: name, virtual_machine where virtual_machine is NOT NULL | ≤4.2.99 |
| logical_service_name_on_parent | name, parent_object_type, parent_object_id | parent_object_type is NOT NULL | Matches on fields: name, parent_object_type, parent_object_id where parent_object_type is NOT NULL | ≥4.3.0 |

## ipam.vlan

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_vlan_vid_no_group_or_svlan | vid | group is NULL AND qinq_svlan is NULL | Matches on fields: vid where group is NULL AND qinq_svlan is NULL | All versions |

## ipam.vlangroup

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_vlan_group_name_no_scope | name | scope_type is NULL | Matches on fields: name where scope_type is NULL | All versions |

## tenancy.contact

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_contact_name | name | N/A | Matches on fields: name | ≥4.3.0 |

## virtualization.cluster

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_cluster_within_scope | name, scope_type, scope_id | scope_type is NOT NULL | Matches on fields: name, scope_type, scope_id where scope_type is NOT NULL | All versions |
| logical_cluster_with_no_scope_or_group | name | group is NULL AND scope_type is NULL | Matches on fields: name where group is NULL AND scope_type is NULL | All versions |

## virtualization.virtualmachine

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_virtual_machine_name_no_cluster | name | cluster is NULL | Matches on fields: name where cluster is NULL | All versions |

## wireless.wirelesslan

| Matcher Name | Fields | Condition | Description | Version Constraints |
|--------------|--------|-----------|-------------|-------------------|
| logical_wireless_lan_ssid_no_group_or_vlan | ssid | group is NULL AND vlan is NULL | Matches on fields: ssid where group is NULL AND vlan is NULL | All versions |
| logical_wireless_lan_ssid_in_group | ssid, group | group is NOT NULL | Matches on fields: ssid, group where group is NOT NULL | All versions |
| logical_wireless_lan_ssid_in_vlan | ssid, vlan | vlan is NOT NULL | Matches on fields: ssid, vlan where vlan is NOT NULL | All versions |

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from netbox_diode_plugin.reconciler.sdk.validate import validate_pb2 as _validate_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Device(_message.Message):
    __slots__ = ("name", "device_fqdn", "device_type", "role", "platform", "serial", "site", "asset_tag", "status", "description", "comments", "tags", "primary_ip4", "primary_ip6")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FQDN_FIELD_NUMBER: _ClassVar[int]
    DEVICE_TYPE_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    PLATFORM_FIELD_NUMBER: _ClassVar[int]
    SERIAL_FIELD_NUMBER: _ClassVar[int]
    SITE_FIELD_NUMBER: _ClassVar[int]
    ASSET_TAG_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_IP4_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_IP6_FIELD_NUMBER: _ClassVar[int]
    name: str
    device_fqdn: str
    device_type: DeviceType
    role: Role
    platform: Platform
    serial: str
    site: Site
    asset_tag: str
    status: str
    description: str
    comments: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    primary_ip4: IPAddress
    primary_ip6: IPAddress
    def __init__(self, name: _Optional[str] = ..., device_fqdn: _Optional[str] = ..., device_type: _Optional[_Union[DeviceType, _Mapping]] = ..., role: _Optional[_Union[Role, _Mapping]] = ..., platform: _Optional[_Union[Platform, _Mapping]] = ..., serial: _Optional[str] = ..., site: _Optional[_Union[Site, _Mapping]] = ..., asset_tag: _Optional[str] = ..., status: _Optional[str] = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ..., primary_ip4: _Optional[_Union[IPAddress, _Mapping]] = ..., primary_ip6: _Optional[_Union[IPAddress, _Mapping]] = ...) -> None: ...

class Interface(_message.Message):
    __slots__ = ("device", "name", "label", "type", "enabled", "mtu", "mac_address", "speed", "wwn", "mgmt_only", "description", "mark_connected", "mode", "tags")
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    MTU_FIELD_NUMBER: _ClassVar[int]
    MAC_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    SPEED_FIELD_NUMBER: _ClassVar[int]
    WWN_FIELD_NUMBER: _ClassVar[int]
    MGMT_ONLY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    MARK_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    device: Device
    name: str
    label: str
    type: str
    enabled: bool
    mtu: int
    mac_address: str
    speed: int
    wwn: str
    mgmt_only: bool
    description: str
    mark_connected: bool
    mode: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, device: _Optional[_Union[Device, _Mapping]] = ..., name: _Optional[str] = ..., label: _Optional[str] = ..., type: _Optional[str] = ..., enabled: bool = ..., mtu: _Optional[int] = ..., mac_address: _Optional[str] = ..., speed: _Optional[int] = ..., wwn: _Optional[str] = ..., mgmt_only: bool = ..., description: _Optional[str] = ..., mark_connected: bool = ..., mode: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Cluster(_message.Message):
    __slots__ = ("name", "type", "group", "site", "status", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    GROUP_FIELD_NUMBER: _ClassVar[int]
    SITE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: ClusterType
    group: ClusterGroup
    site: Site
    status: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., type: _Optional[_Union[ClusterType, _Mapping]] = ..., group: _Optional[_Union[ClusterGroup, _Mapping]] = ..., site: _Optional[_Union[Site, _Mapping]] = ..., status: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class ClusterType(_message.Message):
    __slots__ = ("name", "slug", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class ClusterGroup(_message.Message):
    __slots__ = ("name", "slug", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class VirtualMachine(_message.Message):
    __slots__ = ("name", "status", "site", "cluster", "role", "device", "platform", "primary_ip4", "primary_ip6", "vcpus", "memory", "disk", "description", "comments", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SITE_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    PLATFORM_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_IP4_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_IP6_FIELD_NUMBER: _ClassVar[int]
    VCPUS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    DISK_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    status: str
    site: Site
    cluster: Cluster
    role: Role
    device: Device
    platform: Platform
    primary_ip4: IPAddress
    primary_ip6: IPAddress
    vcpus: int
    memory: int
    disk: int
    description: str
    comments: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., status: _Optional[str] = ..., site: _Optional[_Union[Site, _Mapping]] = ..., cluster: _Optional[_Union[Cluster, _Mapping]] = ..., role: _Optional[_Union[Role, _Mapping]] = ..., device: _Optional[_Union[Device, _Mapping]] = ..., platform: _Optional[_Union[Platform, _Mapping]] = ..., primary_ip4: _Optional[_Union[IPAddress, _Mapping]] = ..., primary_ip6: _Optional[_Union[IPAddress, _Mapping]] = ..., vcpus: _Optional[int] = ..., memory: _Optional[int] = ..., disk: _Optional[int] = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class VMInterface(_message.Message):
    __slots__ = ("virtual_machine", "name", "enabled", "mtu", "mac_address", "description", "tags")
    VIRTUAL_MACHINE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    MTU_FIELD_NUMBER: _ClassVar[int]
    MAC_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    virtual_machine: VirtualMachine
    name: str
    enabled: bool
    mtu: int
    mac_address: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, virtual_machine: _Optional[_Union[VirtualMachine, _Mapping]] = ..., name: _Optional[str] = ..., enabled: bool = ..., mtu: _Optional[int] = ..., mac_address: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class VirtualDisk(_message.Message):
    __slots__ = ("virtual_machine", "name", "size", "description", "tags")
    VIRTUAL_MACHINE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    virtual_machine: VirtualMachine
    name: str
    size: int
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, virtual_machine: _Optional[_Union[VirtualMachine, _Mapping]] = ..., name: _Optional[str] = ..., size: _Optional[int] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class IPAddress(_message.Message):
    __slots__ = ("address", "interface", "status", "role", "dns_name", "description", "comments", "tags")
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    INTERFACE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    DNS_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    address: str
    interface: Interface
    status: str
    role: str
    dns_name: str
    description: str
    comments: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, address: _Optional[str] = ..., interface: _Optional[_Union[Interface, _Mapping]] = ..., status: _Optional[str] = ..., role: _Optional[str] = ..., dns_name: _Optional[str] = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class DeviceType(_message.Message):
    __slots__ = ("model", "slug", "manufacturer", "description", "comments", "part_number", "tags")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    MANUFACTURER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    PART_NUMBER_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    model: str
    slug: str
    manufacturer: Manufacturer
    description: str
    comments: str
    part_number: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, model: _Optional[str] = ..., slug: _Optional[str] = ..., manufacturer: _Optional[_Union[Manufacturer, _Mapping]] = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., part_number: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Manufacturer(_message.Message):
    __slots__ = ("name", "slug", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Platform(_message.Message):
    __slots__ = ("name", "slug", "manufacturer", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    MANUFACTURER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    manufacturer: Manufacturer
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., manufacturer: _Optional[_Union[Manufacturer, _Mapping]] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Prefix(_message.Message):
    __slots__ = ("prefix", "site", "status", "is_pool", "mark_utilized", "description", "comments", "tags")
    PREFIX_FIELD_NUMBER: _ClassVar[int]
    SITE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    IS_POOL_FIELD_NUMBER: _ClassVar[int]
    MARK_UTILIZED_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    prefix: str
    site: Site
    status: str
    is_pool: bool
    mark_utilized: bool
    description: str
    comments: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, prefix: _Optional[str] = ..., site: _Optional[_Union[Site, _Mapping]] = ..., status: _Optional[str] = ..., is_pool: bool = ..., mark_utilized: bool = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Role(_message.Message):
    __slots__ = ("name", "slug", "color", "description", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    color: str
    description: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., color: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Site(_message.Message):
    __slots__ = ("name", "slug", "status", "facility", "time_zone", "description", "comments", "tags")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FACILITY_FIELD_NUMBER: _ClassVar[int]
    TIME_ZONE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    status: str
    facility: str
    time_zone: str
    description: str
    comments: str
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., status: _Optional[str] = ..., facility: _Optional[str] = ..., time_zone: _Optional[str] = ..., description: _Optional[str] = ..., comments: _Optional[str] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class Tag(_message.Message):
    __slots__ = ("name", "slug", "color")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SLUG_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    name: str
    slug: str
    color: str
    def __init__(self, name: _Optional[str] = ..., slug: _Optional[str] = ..., color: _Optional[str] = ...) -> None: ...

class Entity(_message.Message):
    __slots__ = ("site", "platform", "manufacturer", "device", "device_role", "device_type", "interface", "ip_address", "prefix", "cluster_group", "cluster_type", "cluster", "virtual_machine", "vminterface", "virtual_disk", "timestamp")
    SITE_FIELD_NUMBER: _ClassVar[int]
    PLATFORM_FIELD_NUMBER: _ClassVar[int]
    MANUFACTURER_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ROLE_FIELD_NUMBER: _ClassVar[int]
    DEVICE_TYPE_FIELD_NUMBER: _ClassVar[int]
    INTERFACE_FIELD_NUMBER: _ClassVar[int]
    IP_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    PREFIX_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_GROUP_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_TYPE_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_FIELD_NUMBER: _ClassVar[int]
    VIRTUAL_MACHINE_FIELD_NUMBER: _ClassVar[int]
    VMINTERFACE_FIELD_NUMBER: _ClassVar[int]
    VIRTUAL_DISK_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    site: Site
    platform: Platform
    manufacturer: Manufacturer
    device: Device
    device_role: Role
    device_type: DeviceType
    interface: Interface
    ip_address: IPAddress
    prefix: Prefix
    cluster_group: ClusterGroup
    cluster_type: ClusterType
    cluster: Cluster
    virtual_machine: VirtualMachine
    vminterface: VMInterface
    virtual_disk: VirtualDisk
    timestamp: _timestamp_pb2.Timestamp
    def __init__(self, site: _Optional[_Union[Site, _Mapping]] = ..., platform: _Optional[_Union[Platform, _Mapping]] = ..., manufacturer: _Optional[_Union[Manufacturer, _Mapping]] = ..., device: _Optional[_Union[Device, _Mapping]] = ..., device_role: _Optional[_Union[Role, _Mapping]] = ..., device_type: _Optional[_Union[DeviceType, _Mapping]] = ..., interface: _Optional[_Union[Interface, _Mapping]] = ..., ip_address: _Optional[_Union[IPAddress, _Mapping]] = ..., prefix: _Optional[_Union[Prefix, _Mapping]] = ..., cluster_group: _Optional[_Union[ClusterGroup, _Mapping]] = ..., cluster_type: _Optional[_Union[ClusterType, _Mapping]] = ..., cluster: _Optional[_Union[Cluster, _Mapping]] = ..., virtual_machine: _Optional[_Union[VirtualMachine, _Mapping]] = ..., vminterface: _Optional[_Union[VMInterface, _Mapping]] = ..., virtual_disk: _Optional[_Union[VirtualDisk, _Mapping]] = ..., timestamp: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class IngestRequest(_message.Message):
    __slots__ = ("stream", "entities", "id", "producer_app_name", "producer_app_version", "sdk_name", "sdk_version")
    STREAM_FIELD_NUMBER: _ClassVar[int]
    ENTITIES_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_APP_NAME_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_APP_VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_NAME_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    stream: str
    entities: _containers.RepeatedCompositeFieldContainer[Entity]
    id: str
    producer_app_name: str
    producer_app_version: str
    sdk_name: str
    sdk_version: str
    def __init__(self, stream: _Optional[str] = ..., entities: _Optional[_Iterable[_Union[Entity, _Mapping]]] = ..., id: _Optional[str] = ..., producer_app_name: _Optional[str] = ..., producer_app_version: _Optional[str] = ..., sdk_name: _Optional[str] = ..., sdk_version: _Optional[str] = ...) -> None: ...

class IngestResponse(_message.Message):
    __slots__ = ("errors",)
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    errors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, errors: _Optional[_Iterable[str]] = ...) -> None: ...

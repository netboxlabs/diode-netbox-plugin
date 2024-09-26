from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2 as _ingester_pb2
from netbox_diode_plugin.reconciler.sdk.validate import validate_pb2 as _validate_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class State(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    UNSPECIFIED: _ClassVar[State]
    QUEUED: _ClassVar[State]
    RECONCILED: _ClassVar[State]
    FAILED: _ClassVar[State]
    NO_CHANGES: _ClassVar[State]
UNSPECIFIED: State
QUEUED: State
RECONCILED: State
FAILED: State
NO_CHANGES: State

class IngestionDataSource(_message.Message):
    __slots__ = ("name", "api_key")
    NAME_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    name: str
    api_key: str
    def __init__(self, name: _Optional[str] = ..., api_key: _Optional[str] = ...) -> None: ...

class RetrieveIngestionDataSourcesRequest(_message.Message):
    __slots__ = ("name", "sdk_name", "sdk_version")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SDK_NAME_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    name: str
    sdk_name: str
    sdk_version: str
    def __init__(self, name: _Optional[str] = ..., sdk_name: _Optional[str] = ..., sdk_version: _Optional[str] = ...) -> None: ...

class RetrieveIngestionDataSourcesResponse(_message.Message):
    __slots__ = ("ingestion_data_sources",)
    INGESTION_DATA_SOURCES_FIELD_NUMBER: _ClassVar[int]
    ingestion_data_sources: _containers.RepeatedCompositeFieldContainer[IngestionDataSource]
    def __init__(self, ingestion_data_sources: _Optional[_Iterable[_Union[IngestionDataSource, _Mapping]]] = ...) -> None: ...

class IngestionError(_message.Message):
    __slots__ = ("message", "code", "details")
    class Details(_message.Message):
        __slots__ = ("change_set_id", "result", "errors")
        class Error(_message.Message):
            __slots__ = ("error", "change_id")
            ERROR_FIELD_NUMBER: _ClassVar[int]
            CHANGE_ID_FIELD_NUMBER: _ClassVar[int]
            error: str
            change_id: str
            def __init__(self, error: _Optional[str] = ..., change_id: _Optional[str] = ...) -> None: ...
        CHANGE_SET_ID_FIELD_NUMBER: _ClassVar[int]
        RESULT_FIELD_NUMBER: _ClassVar[int]
        ERRORS_FIELD_NUMBER: _ClassVar[int]
        change_set_id: str
        result: str
        errors: _containers.RepeatedCompositeFieldContainer[IngestionError.Details.Error]
        def __init__(self, change_set_id: _Optional[str] = ..., result: _Optional[str] = ..., errors: _Optional[_Iterable[_Union[IngestionError.Details.Error, _Mapping]]] = ...) -> None: ...
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    message: str
    code: int
    details: IngestionError.Details
    def __init__(self, message: _Optional[str] = ..., code: _Optional[int] = ..., details: _Optional[_Union[IngestionError.Details, _Mapping]] = ...) -> None: ...

class IngestionMetrics(_message.Message):
    __slots__ = ("total", "queued", "reconciled", "failed", "no_changes")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    QUEUED_FIELD_NUMBER: _ClassVar[int]
    RECONCILED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    NO_CHANGES_FIELD_NUMBER: _ClassVar[int]
    total: int
    queued: int
    reconciled: int
    failed: int
    no_changes: int
    def __init__(self, total: _Optional[int] = ..., queued: _Optional[int] = ..., reconciled: _Optional[int] = ..., failed: _Optional[int] = ..., no_changes: _Optional[int] = ...) -> None: ...

class ChangeSet(_message.Message):
    __slots__ = ("id", "data")
    ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    data: bytes
    def __init__(self, id: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class IngestionLog(_message.Message):
    __slots__ = ("id", "data_type", "state", "request_id", "ingestion_ts", "producer_app_name", "producer_app_version", "sdk_name", "sdk_version", "entity", "error", "change_set")
    ID_FIELD_NUMBER: _ClassVar[int]
    DATA_TYPE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_APP_NAME_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_APP_VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_NAME_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    ENTITY_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CHANGE_SET_FIELD_NUMBER: _ClassVar[int]
    id: str
    data_type: str
    state: State
    request_id: str
    ingestion_ts: int
    producer_app_name: str
    producer_app_version: str
    sdk_name: str
    sdk_version: str
    entity: _ingester_pb2.Entity
    error: IngestionError
    change_set: ChangeSet
    def __init__(self, id: _Optional[str] = ..., data_type: _Optional[str] = ..., state: _Optional[_Union[State, str]] = ..., request_id: _Optional[str] = ..., ingestion_ts: _Optional[int] = ..., producer_app_name: _Optional[str] = ..., producer_app_version: _Optional[str] = ..., sdk_name: _Optional[str] = ..., sdk_version: _Optional[str] = ..., entity: _Optional[_Union[_ingester_pb2.Entity, _Mapping]] = ..., error: _Optional[_Union[IngestionError, _Mapping]] = ..., change_set: _Optional[_Union[ChangeSet, _Mapping]] = ...) -> None: ...

class RetrieveIngestionLogsRequest(_message.Message):
    __slots__ = ("page_size", "state", "data_type", "request_id", "ingestion_ts_start", "ingestion_ts_end", "page_token", "only_metrics")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    DATA_TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_START_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_END_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ONLY_METRICS_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    state: State
    data_type: str
    request_id: str
    ingestion_ts_start: int
    ingestion_ts_end: int
    page_token: str
    only_metrics: bool
    def __init__(self, page_size: _Optional[int] = ..., state: _Optional[_Union[State, str]] = ..., data_type: _Optional[str] = ..., request_id: _Optional[str] = ..., ingestion_ts_start: _Optional[int] = ..., ingestion_ts_end: _Optional[int] = ..., page_token: _Optional[str] = ..., only_metrics: bool = ...) -> None: ...

class RetrieveIngestionLogsResponse(_message.Message):
    __slots__ = ("logs", "metrics", "next_page_token")
    LOGS_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[IngestionLog]
    metrics: IngestionMetrics
    next_page_token: str
    def __init__(self, logs: _Optional[_Iterable[_Union[IngestionLog, _Mapping]]] = ..., metrics: _Optional[_Union[IngestionMetrics, _Mapping]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

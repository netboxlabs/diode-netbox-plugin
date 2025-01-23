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
    STATE_UNSPECIFIED: _ClassVar[State]
    QUEUED: _ClassVar[State]
    OPEN: _ClassVar[State]
    APPLIED: _ClassVar[State]
    FAILED: _ClassVar[State]
    NO_CHANGES: _ClassVar[State]
    IGNORED: _ClassVar[State]
    ERRORED: _ClassVar[State]
STATE_UNSPECIFIED: State
QUEUED: State
OPEN: State
APPLIED: State
FAILED: State
NO_CHANGES: State
IGNORED: State
ERRORED: State

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
    __slots__ = ("id", "data", "branch_id", "deviation_name")
    ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    BRANCH_ID_FIELD_NUMBER: _ClassVar[int]
    DEVIATION_NAME_FIELD_NUMBER: _ClassVar[int]
    id: str
    data: bytes
    branch_id: str
    deviation_name: str
    def __init__(self, id: _Optional[str] = ..., data: _Optional[bytes] = ..., branch_id: _Optional[str] = ..., deviation_name: _Optional[str] = ...) -> None: ...

class IngestionLog(_message.Message):
    __slots__ = ("id", "data_type", "state", "request_id", "ingestion_ts", "producer_app_name", "producer_app_version", "sdk_name", "sdk_version", "entity", "error", "change_set", "object_type")
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
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
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
    object_type: str
    def __init__(self, id: _Optional[str] = ..., data_type: _Optional[str] = ..., state: _Optional[_Union[State, str]] = ..., request_id: _Optional[str] = ..., ingestion_ts: _Optional[int] = ..., producer_app_name: _Optional[str] = ..., producer_app_version: _Optional[str] = ..., sdk_name: _Optional[str] = ..., sdk_version: _Optional[str] = ..., entity: _Optional[_Union[_ingester_pb2.Entity, _Mapping]] = ..., error: _Optional[_Union[IngestionError, _Mapping]] = ..., change_set: _Optional[_Union[ChangeSet, _Mapping]] = ..., object_type: _Optional[str] = ...) -> None: ...

class RetrieveIngestionLogsRequest(_message.Message):
    __slots__ = ("page_size", "state", "data_type", "request_id", "ingestion_ts_start", "ingestion_ts_end", "page_token", "only_metrics", "object_type")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    DATA_TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_START_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_END_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ONLY_METRICS_FIELD_NUMBER: _ClassVar[int]
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    state: State
    data_type: str
    request_id: str
    ingestion_ts_start: int
    ingestion_ts_end: int
    page_token: str
    only_metrics: bool
    object_type: str
    def __init__(self, page_size: _Optional[int] = ..., state: _Optional[_Union[State, str]] = ..., data_type: _Optional[str] = ..., request_id: _Optional[str] = ..., ingestion_ts_start: _Optional[int] = ..., ingestion_ts_end: _Optional[int] = ..., page_token: _Optional[str] = ..., only_metrics: bool = ..., object_type: _Optional[str] = ...) -> None: ...

class RetrieveIngestionLogsResponse(_message.Message):
    __slots__ = ("logs", "metrics", "next_page_token")
    LOGS_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[IngestionLog]
    metrics: IngestionMetrics
    next_page_token: str
    def __init__(self, logs: _Optional[_Iterable[_Union[IngestionLog, _Mapping]]] = ..., metrics: _Optional[_Union[IngestionMetrics, _Mapping]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class RetrieveDeviationsRequest(_message.Message):
    __slots__ = ("page_size", "page_token", "ingestion_ts_start", "ingestion_ts_end", "state", "object_type", "branch_id")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_START_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_END_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    BRANCH_ID_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    page_token: str
    ingestion_ts_start: int
    ingestion_ts_end: int
    state: _containers.RepeatedScalarFieldContainer[State]
    object_type: _containers.RepeatedScalarFieldContainer[str]
    branch_id: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., ingestion_ts_start: _Optional[int] = ..., ingestion_ts_end: _Optional[int] = ..., state: _Optional[_Iterable[_Union[State, str]]] = ..., object_type: _Optional[_Iterable[str]] = ..., branch_id: _Optional[_Iterable[str]] = ...) -> None: ...

class DeviationError(_message.Message):
    __slots__ = ("message", "code")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    message: str
    code: int
    def __init__(self, message: _Optional[str] = ..., code: _Optional[int] = ...) -> None: ...

class Change(_message.Message):
    __slots__ = ("id", "object_type", "object_primary_value", "change_type", "before", "after")
    ID_FIELD_NUMBER: _ClassVar[int]
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_PRIMARY_VALUE_FIELD_NUMBER: _ClassVar[int]
    CHANGE_TYPE_FIELD_NUMBER: _ClassVar[int]
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    id: str
    object_type: str
    object_primary_value: str
    change_type: str
    before: bytes
    after: bytes
    def __init__(self, id: _Optional[str] = ..., object_type: _Optional[str] = ..., object_primary_value: _Optional[str] = ..., change_type: _Optional[str] = ..., before: _Optional[bytes] = ..., after: _Optional[bytes] = ...) -> None: ...

class Deviation(_message.Message):
    __slots__ = ("id", "ingestion_ts", "last_update_ts", "name", "source", "state", "object_type", "branch_id", "ingested_entity", "error", "changes")
    ID_FIELD_NUMBER: _ClassVar[int]
    INGESTION_TS_FIELD_NUMBER: _ClassVar[int]
    LAST_UPDATE_TS_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    BRANCH_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTED_ENTITY_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CHANGES_FIELD_NUMBER: _ClassVar[int]
    id: str
    ingestion_ts: int
    last_update_ts: int
    name: str
    source: str
    state: State
    object_type: str
    branch_id: str
    ingested_entity: _ingester_pb2.Entity
    error: DeviationError
    changes: _containers.RepeatedCompositeFieldContainer[Change]
    def __init__(self, id: _Optional[str] = ..., ingestion_ts: _Optional[int] = ..., last_update_ts: _Optional[int] = ..., name: _Optional[str] = ..., source: _Optional[str] = ..., state: _Optional[_Union[State, str]] = ..., object_type: _Optional[str] = ..., branch_id: _Optional[str] = ..., ingested_entity: _Optional[_Union[_ingester_pb2.Entity, _Mapping]] = ..., error: _Optional[_Union[DeviationError, _Mapping]] = ..., changes: _Optional[_Iterable[_Union[Change, _Mapping]]] = ...) -> None: ...

class RetrieveDeviationsResponse(_message.Message):
    __slots__ = ("deviations", "next_page_token")
    DEVIATIONS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    deviations: _containers.RepeatedCompositeFieldContainer[Deviation]
    next_page_token: str
    def __init__(self, deviations: _Optional[_Iterable[_Union[Deviation, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class RetrieveDeviationByIDRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class RetrieveDeviationByIDResponse(_message.Message):
    __slots__ = ("deviation",)
    DEVIATION_FIELD_NUMBER: _ClassVar[int]
    deviation: Deviation
    def __init__(self, deviation: _Optional[_Union[Deviation, _Mapping]] = ...) -> None: ...

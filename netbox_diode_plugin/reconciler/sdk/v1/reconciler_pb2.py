# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: diode/v1/reconciler.proto
# Protobuf Python Version: 5.26.1
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2 as diode_dot_v1_dot_ingester__pb2
from netbox_diode_plugin.reconciler.sdk.validate import validate_pb2 as validate_dot_validate__pb2


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x19\x64iode/v1/reconciler.proto\x12\x08\x64iode.v1\x1a\x17\x64iode/v1/ingester.proto\x1a\x17validate/validate.proto\"Y\n\x13IngestionDataSource\x12\x1e\n\x04name\x18\x01 \x01(\tB\n\xfa\x42\x07r\x05\x10\x01\x18\xff\x01R\x04name\x12\"\n\x07\x61pi_key\x18\x02 \x01(\tB\t\xfa\x42\x06r\x04\x10(\x18(R\x06\x61piKey\"\xab\x01\n#RetrieveIngestionDataSourcesRequest\x12\x1e\n\x04name\x18\x01 \x01(\tB\n\xfa\x42\x07r\x05\x10\x01\x18\xff\x01R\x04name\x12%\n\x08sdk_name\x18\x02 \x01(\tB\n\xfa\x42\x07r\x05\x10\x01\x18\xff\x01R\x07sdkName\x12=\n\x0bsdk_version\x18\x03 \x01(\tB\x1c\xfa\x42\x19r\x17\x32\x15^(\\d)+\\.(\\d)+\\.(\\d)+$R\nsdkVersion\"{\n$RetrieveIngestionDataSourcesResponse\x12S\n\x16ingestion_data_sources\x18\x01 \x03(\x0b\x32\x1d.diode.v1.IngestionDataSourceR\x14ingestionDataSources\"\xbe\x02\n\x0e\x43hangeSetError\x12\x18\n\x07message\x18\x01 \x01(\tR\x07message\x12\x12\n\x04\x63ode\x18\x02 \x01(\x05R\x04\x63ode\x12:\n\x07\x64\x65tails\x18\x03 \x01(\x0b\x32 .diode.v1.ChangeSetError.DetailsR\x07\x64\x65tails\x1a\xc1\x01\n\x07\x44\x65tails\x12\"\n\rchange_set_id\x18\x01 \x01(\tR\x0b\x63hangeSetId\x12\x16\n\x06result\x18\x02 \x01(\tR\x06result\x12>\n\x06\x65rrors\x18\x03 \x03(\x0b\x32&.diode.v1.ChangeSetError.Details.ErrorR\x06\x65rrors\x1a:\n\x05\x45rror\x12\x14\n\x05\x65rror\x18\x01 \x01(\tR\x05\x65rror\x12\x1b\n\tchange_id\x18\x02 \x01(\tR\x08\x63hangeId\"\x88\x03\n\x0cIngestionLog\x12\x1b\n\tdata_type\x18\x01 \x01(\tR\x08\x64\x61taType\x12%\n\x05state\x18\x02 \x01(\x0e\x32\x0f.diode.v1.StateR\x05state\x12\x1d\n\nrequest_id\x18\x03 \x01(\tR\trequestId\x12!\n\x0cingestion_ts\x18\x04 \x01(\x03R\x0bingestionTs\x12*\n\x11producer_app_name\x18\x05 \x01(\tR\x0fproducerAppName\x12\x30\n\x14producer_app_version\x18\x06 \x01(\tR\x12producerAppVersion\x12\x19\n\x08sdk_name\x18\x07 \x01(\tR\x07sdkName\x12\x1f\n\x0bsdk_version\x18\x08 \x01(\tR\nsdkVersion\x12(\n\x06\x65ntity\x18\t \x01(\x0b\x32\x10.diode.v1.EntityR\x06\x65ntity\x12.\n\x05\x65rror\x18\n \x01(\x0b\x32\x18.diode.v1.ChangeSetErrorR\x05\x65rror\"\xb0\x02\n\x1cRetrieveIngestionLogsRequest\x12\'\n\tpage_size\x18\x01 \x01(\x05\x42\n\xfa\x42\x07\x1a\x05\x18\xe8\x07(\x01R\x08pageSize\x12*\n\x05state\x18\x02 \x01(\x0e\x32\x0f.diode.v1.StateH\x00R\x05state\x88\x01\x01\x12\x1b\n\tdata_type\x18\x03 \x01(\tR\x08\x64\x61taType\x12\x1d\n\nrequest_id\x18\x04 \x01(\tR\trequestId\x12,\n\x12ingestion_ts_start\x18\x05 \x01(\x03R\x10ingestionTsStart\x12(\n\x10ingestion_ts_end\x18\x06 \x01(\x03R\x0eingestionTsEnd\x12\x1d\n\npage_token\x18\x07 \x01(\tR\tpageTokenB\x08\n\x06_state\"s\n\x1dRetrieveIngestionLogsResponse\x12*\n\x04logs\x18\x01 \x03(\x0b\x32\x16.diode.v1.IngestionLogR\x04logs\x12&\n\x0fnext_page_token\x18\x02 \x01(\tR\rnextPageToken*<\n\x05State\x12\x07\n\x03NEW\x10\x00\x12\x0e\n\nRECONCILED\x10\x01\x12\n\n\x06\x46\x41ILED\x10\x02\x12\x0e\n\nNO_CHANGES\x10\x03\x32\xfe\x01\n\x11ReconcilerService\x12\x7f\n\x1cRetrieveIngestionDataSources\x12-.diode.v1.RetrieveIngestionDataSourcesRequest\x1a..diode.v1.RetrieveIngestionDataSourcesResponse\"\x00\x12h\n\x15RetrieveIngestionLogs\x12&.diode.v1.RetrieveIngestionLogsRequest\x1a\'.diode.v1.RetrieveIngestionLogsResponseBDZBgithub.com/netboxlabs/diode/diode-server/gen/diode/v1/reconcilerpbb\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'diode.v1.reconciler_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  _globals['DESCRIPTOR']._loaded_options = None
  _globals['DESCRIPTOR']._serialized_options = b'ZBgithub.com/netboxlabs/diode/diode-server/gen/diode/v1/reconcilerpb'
  _globals['_INGESTIONDATASOURCE'].fields_by_name['name']._loaded_options = None
  _globals['_INGESTIONDATASOURCE'].fields_by_name['name']._serialized_options = b'\372B\007r\005\020\001\030\377\001'
  _globals['_INGESTIONDATASOURCE'].fields_by_name['api_key']._loaded_options = None
  _globals['_INGESTIONDATASOURCE'].fields_by_name['api_key']._serialized_options = b'\372B\006r\004\020(\030('
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['name']._loaded_options = None
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['name']._serialized_options = b'\372B\007r\005\020\001\030\377\001'
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['sdk_name']._loaded_options = None
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['sdk_name']._serialized_options = b'\372B\007r\005\020\001\030\377\001'
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['sdk_version']._loaded_options = None
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST'].fields_by_name['sdk_version']._serialized_options = b'\372B\031r\0272\025^(\\d)+\\.(\\d)+\\.(\\d)+$'
  _globals['_RETRIEVEINGESTIONLOGSREQUEST'].fields_by_name['page_size']._loaded_options = None
  _globals['_RETRIEVEINGESTIONLOGSREQUEST'].fields_by_name['page_size']._serialized_options = b'\372B\007\032\005\030\350\007(\001'
  _globals['_STATE']._serialized_start=1619
  _globals['_STATE']._serialized_end=1679
  _globals['_INGESTIONDATASOURCE']._serialized_start=89
  _globals['_INGESTIONDATASOURCE']._serialized_end=178
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST']._serialized_start=181
  _globals['_RETRIEVEINGESTIONDATASOURCESREQUEST']._serialized_end=352
  _globals['_RETRIEVEINGESTIONDATASOURCESRESPONSE']._serialized_start=354
  _globals['_RETRIEVEINGESTIONDATASOURCESRESPONSE']._serialized_end=477
  _globals['_CHANGESETERROR']._serialized_start=480
  _globals['_CHANGESETERROR']._serialized_end=798
  _globals['_CHANGESETERROR_DETAILS']._serialized_start=605
  _globals['_CHANGESETERROR_DETAILS']._serialized_end=798
  _globals['_CHANGESETERROR_DETAILS_ERROR']._serialized_start=740
  _globals['_CHANGESETERROR_DETAILS_ERROR']._serialized_end=798
  _globals['_INGESTIONLOG']._serialized_start=801
  _globals['_INGESTIONLOG']._serialized_end=1193
  _globals['_RETRIEVEINGESTIONLOGSREQUEST']._serialized_start=1196
  _globals['_RETRIEVEINGESTIONLOGSREQUEST']._serialized_end=1500
  _globals['_RETRIEVEINGESTIONLOGSRESPONSE']._serialized_start=1502
  _globals['_RETRIEVEINGESTIONLOGSRESPONSE']._serialized_end=1617
  _globals['_RECONCILERSERVICE']._serialized_start=1682
  _globals['_RECONCILERSERVICE']._serialized_end=1936
# @@protoc_insertion_point(module_scope)

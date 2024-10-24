# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

from netbox_diode_plugin.reconciler.sdk.v1 import reconciler_pb2 as diode_dot_v1_dot_reconciler__pb2


class ReconcilerServiceStub(object):
    """Reconciler service API
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.RetrieveIngestionDataSources = channel.unary_unary(
                '/diode.v1.ReconcilerService/RetrieveIngestionDataSources',
                request_serializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesRequest.SerializeToString,
                response_deserializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesResponse.FromString,
                )
        self.RetrieveIngestionLogs = channel.unary_unary(
                '/diode.v1.ReconcilerService/RetrieveIngestionLogs',
                request_serializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsRequest.SerializeToString,
                response_deserializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsResponse.FromString,
                )


class ReconcilerServiceServicer(object):
    """Reconciler service API
    """

    def RetrieveIngestionDataSources(self, request, context):
        """Retrieves ingestion data sources
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def RetrieveIngestionLogs(self, request, context):
        """Retrieves ingestion logs
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_ReconcilerServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'RetrieveIngestionDataSources': grpc.unary_unary_rpc_method_handler(
                    servicer.RetrieveIngestionDataSources,
                    request_deserializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesRequest.FromString,
                    response_serializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesResponse.SerializeToString,
            ),
            'RetrieveIngestionLogs': grpc.unary_unary_rpc_method_handler(
                    servicer.RetrieveIngestionLogs,
                    request_deserializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsRequest.FromString,
                    response_serializer=diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'diode.v1.ReconcilerService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))


 # This class is part of an EXPERIMENTAL API.
class ReconcilerService(object):
    """Reconciler service API
    """

    @staticmethod
    def RetrieveIngestionDataSources(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/diode.v1.ReconcilerService/RetrieveIngestionDataSources',
            diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesRequest.SerializeToString,
            diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionDataSourcesResponse.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def RetrieveIngestionLogs(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/diode.v1.ReconcilerService/RetrieveIngestionLogs',
            diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsRequest.SerializeToString,
            diode_dot_v1_dot_reconciler__pb2.RetrieveIngestionLogsResponse.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

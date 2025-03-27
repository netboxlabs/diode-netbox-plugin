#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API Views."""
import json
import logging
import re
from typing import Any, Dict, Optional

from django.apps import apps
from django.conf import settings
from packaging import version

if version.parse(settings.VERSION).major >= 4:
    from core.models import ObjectType as NetBoxType
else:
    from django.contrib.contenttypes.models import ContentType as NetBoxType

from django.core.exceptions import FieldError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.db.models import Q
from rest_framework import status, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from utilities.api import get_serializer_for_model

from netbox_diode_plugin.api.permissions import IsDiodeReader, IsDiodeWriter
from netbox_diode_plugin.api.serializers import ApplyChangeSetRequestSerializer, ObjectStateSerializer
from netbox_diode_plugin.api.differ import generate_changeset

logger = logging.getLogger("netbox.diode_data")

# Try to import Branch model at module level
Branch = None
try:
    if apps.is_installed("netbox_branching"):
        from netbox_branching.models import Branch
except ImportError:
    logger.warning(
        "netbox_branching plugin is installed but models could not be imported"
    )

def dynamic_import(name):
    """Dynamically import a class from an absolute path string."""
    components = name.split(".")
    mod = __import__(components[0])
    for comp in components[1:]:
        mod = getattr(mod, comp)
    return mod


def _get_index_class_fields(object_type: str | NetBoxType):
    """
    Given an object type name (e.g., 'dcim.site'), dynamically find and return the corresponding Index class fields.

    :param object_type: Object type name in the format 'app_label.model_name'
    :return: The corresponding model and its Index class (e.g., SiteIndex) field names or None.
    """
    try:
        if isinstance(object_type, str):
            app_label, model_name = object_type.split('.')
        else:
            app_label, model_name = object_type.app_label, object_type.model

        model = apps.get_model(app_label, model_name)

        if app_label == "extras" and model_name == "tag":
            app_label = "netbox_diode_plugin"

        index_module = dynamic_import(f"{app_label}.search.{model.__name__}Index")
        fields = getattr(index_module, "fields", None)
        field_names = [field[0] for field in fields]

        return model, field_names

    except (LookupError, ModuleNotFoundError, AttributeError, ValueError):
        return None, None

def _validate_model_instance_fields(instance, fields, value):
    """
    Validate the model instance fields against the value.

    :param instance: The model instance.
    :param fields: The fields of the model instance.
    :param value: The value to validate against the model instance fields.
    :return: fields list passed validation
    """
    errors = {}

    # Set provided values to the instance fields
    for field in fields:
        if hasattr(instance, field):
            # get the field type
            field_cls = instance._meta.get_field(field).__class__

            field_value = _convert_field_value(field_cls, value)
            setattr(instance, field, field_value)

    # Attempt to validate the instance
    try:
        instance.clean_fields()
    except DjangoValidationError as e:
        errors = e.message_dict
    return errors

def _convert_field_value(field_cls, value):
    """Return the converted field value based on the field type."""
    if value is None:
        return value

    try:
        if issubclass(field_cls, (models.FloatField, models.DecimalField)):
            return float(value)
        if issubclass(field_cls, models.IntegerField):
            return int(value)
    except (ValueError, TypeError):
        pass

    return value


class ObjectStateView(views.APIView):
    """ObjectState view."""

    permission_classes = [IsAuthenticated, IsDiodeReader]

    def _get_lookups(self, object_type_model: str) -> tuple:
        """
        This method returns a tuple of related object lookups based on the provided object type model.

        Args:
        ----
            object_type_model (str): The name of the object type model.

        Returns:
        -------
            tuple: A tuple of related object lookups. The tuple is empty if the object type model does not match any
            of the specified models.

        """
        if "'ipam.models.ip.ipaddress'" in object_type_model:
            return (
                "assigned_object",
                "assigned_object__device",
                "assigned_object__device__site",
            )
        if "'dcim.models.device_components.interface'" in object_type_model:
            return "device", "device__site"
        if "'dcim.models.devices.device'" in object_type_model:
            return ("site",)
        return ()

    def _search_queryset(self, request):
        """Search for objects according to object type using search index classes."""
        object_type = request.GET.get("object_type", None)
        object_id = request.GET.get("id", None)
        query = request.GET.get("q", None)

        if not object_type:
            raise ValidationError("object_type parameter is required")

        if not object_id and not query:
            raise ValidationError("id or q parameter is required")

        model, fields = _get_index_class_fields(object_type)

        if object_id:
            queryset = model.objects.filter(id=object_id)
        else:
            q = Q()

            invalid_fields = _validate_model_instance_fields(model(), fields, query)

            fields = [field for field in fields if field not in invalid_fields]

            for field in fields:
                q |= Q(**{f"{field}__exact": query})  # Exact match

            try:
                queryset = model.objects.filter(q)
            except DjangoValidationError:
                queryset = model.objects.none()
                pass

            lookups = self._get_lookups(str(model).lower())

            if lookups:
                queryset = queryset.prefetch_related(*lookups)

            additional_attributes_query_filter = (
                self._additional_attributes_query_filter()
            )

            if additional_attributes_query_filter:
                queryset = queryset.filter(**additional_attributes_query_filter)

        return queryset

    def get(self, request, *args, **kwargs):
        """
        Return a JSON with object_type, object_change_id, and object.

        Search for objects according to object type.
        If the obj_type parameter is not in the parameters, raise a ValidationError.
        When object ID is provided in the request, search using it in the model specified by object type.
        If ID is not provided, use the q parameter for searching.
        Lookup is iexact
        """
        try:
            queryset = self._search_queryset(request)
        except (FieldError, ValueError):
            return Response(
                {"errors": ["invalid additional attributes provided"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.check_object_permissions(request, queryset)

        object_type = request.GET.get("object_type", None)

        serializer = ObjectStateSerializer(
            queryset,
            many=True,
            context={
                "request": request,
                "object_type": f"{object_type}",
            },
        )

        try:
            if len(serializer.data) > 0:
                return Response(serializer.data[0])
            return Response({})
        except AttributeError as e:
            return Response(
                {"errors": [f"Serializer error: {e.args[0]}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _additional_attributes_query_filter(self):
        """Get the additional attributes query filter."""
        additional_attributes = {}
        for attr in self.request.query_params:
            if attr not in ["object_type", "id", "q", "_branch"]:
                additional_attributes[attr] = self.request.query_params.get(attr)

        return dict(additional_attributes.items())


class ApplyChangeSetView(views.APIView):
    """ApplyChangeSet view."""

    permission_classes = [IsAuthenticated, IsDiodeWriter]

    @staticmethod
    def _get_object_type_model(object_type: str | NetBoxType):
        """Get the object type model from object_type."""
        if isinstance(object_type, str):
            app_label, model_name = object_type.split(".")
            object_content_type = NetBoxType.objects.get_by_natural_key(app_label, model_name)
        else:
            object_content_type = object_type
        return object_content_type, object_content_type.model_class()

    def _get_assigned_object_type(self, model_name: str):
        """Get the object type model from applied IPAddress assigned object."""
        assignable_object_types = {
            "interface": "dcim.interface",
        }
        return assignable_object_types.get(model_name.lower(), None)

    def _add_nested_opts(self, fields, key, value):
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                self._add_nested_opts(fields, f"{key}__{nested_key}", nested_value)
        elif not isinstance(value, list):
            fields[key] = value

    def _get_serializer(
        self,
        change_type: str,
        object_id: int,
        object_type: str,
        object_data: dict,
    ):
        """Get the serializer for the object type."""
        _, object_type_model_class = self._get_object_type_model(object_type)

        if change_type == "create":
            return self._get_serializer_to_create(object_data, object_type, object_type_model_class)

        if change_type == "update":
            return self._get_serializer_to_update(object_data, object_id, object_type, object_type_model_class)

        raise ValidationError("Invalid change_type")

    def _get_serializer_to_create(self, object_data, object_type, object_type_model_class):
        # Get object data fields that are not dictionaries or lists
        fields = self._get_fields_to_find_existing_objects(object_data, object_type)
        # Check if the object already exists
        try:
            instance = object_type_model_class.objects.get(**fields)
            return get_serializer_for_model(object_type_model_class)(
                instance, data=object_data, context={"request": self.request, "pk": instance.pk}
            )
        except object_type_model_class.DoesNotExist:
            pass
        serializer = get_serializer_for_model(object_type_model_class)(
            data=object_data, context={"request": self.request}
        )
        return serializer

    def _get_serializer_to_update(self, object_data, object_id, object_type, object_type_model_class):
        lookups = ()
        fields = {}
        primary_ip_to_set: Optional[dict] = None
        if object_id:
            fields["id"] = object_id
        elif object_type == "dcim.device" and any(
                object_data.get(attr) for attr in ("primary_ip4", "primary_ip6")
        ):
            ip_address = self._retrieve_primary_ip_address(
                "primary_ip4", object_data
            )

            if ip_address is None:
                ip_address = self._retrieve_primary_ip_address(
                    "primary_ip6", object_data
                )

            if ip_address is None:
                raise ValidationError("primary IP not found")

            if ip_address:
                primary_ip_to_set = {
                    "id": ip_address.id,
                    "family": ip_address.family,
                }

            lookups = ("site",)
            fields["name"] = object_data.get("name")
            fields["site__name"] = object_data.get("site").get("name")
        else:
            raise ValidationError("object_id parameter is required")
        try:
            instance = object_type_model_class.objects.prefetch_related(*lookups).get(**fields)
            if object_type == "dcim.device" and primary_ip_to_set:
                object_data = {
                    "id": instance.id,
                    "device_type": instance.device_type.id,
                    "role": instance.role.id,
                    "site": instance.site.id,
                    f'primary_ip{primary_ip_to_set.get("family")}': primary_ip_to_set.get(
                        "id"
                    ),
                }
        except object_type_model_class.DoesNotExist:
            raise ValidationError(f"object with id {object_id} does not exist")
        serializer = get_serializer_for_model(object_type_model_class)(
            instance, data=object_data, context={"request": self.request}
        )
        return serializer

    def _get_fields_to_find_existing_objects(self, object_data, object_type):
        fields = {}
        for key, value in object_data.items():
            self._add_nested_opts(fields, key, value)

        match object_type:
            case "dcim.interface" | "virtualization.vminterface":
                mac_address = fields.pop("mac_address", None)
                if mac_address is not None:
                    fields["primary_mac_address__mac_address"] = mac_address
            case "ipam.ipaddress":
                fields.pop("assigned_object_type")
                fields["assigned_object_type_id"] = fields.pop("assigned_object_id")
            case "ipam.prefix" | "virtualization.cluster":
                if scope_type := object_data.get("scope_type"):
                    scope_type_model, _ = self._get_object_type_model(scope_type)
                    fields["scope_type"] = scope_type_model
            case "virtualization.virtualmachine":
                if cluster_scope_type := fields.get("cluster__scope_type"):
                    cluster_scope_type_model, _ = self._get_object_type_model(cluster_scope_type)
                    fields["cluster__scope_type"] = cluster_scope_type_model
            case "virtualization.vminterface":
                if cluster_scope_type := fields.get("virtual_machine__cluster__scope_type"):
                    cluster_scope_type_model, _ = self._get_object_type_model(cluster_scope_type)
                    fields["virtual_machine__cluster__scope_type"] = cluster_scope_type_model

        return fields

    def _retrieve_primary_ip_address(self, primary_ip_attr: str, object_data: dict):
        """Retrieve the primary IP address object."""
        ip_address = object_data.get(primary_ip_attr)
        if ip_address is None:
            return None

        ipaddress_assigned_object = object_data.get(primary_ip_attr, {}).get(
            "assigned_object", None
        )
        if ipaddress_assigned_object is None:
            return None

        interface = ipaddress_assigned_object.get("interface")
        if interface is None:
            return None

        interface_device = interface.get("device")
        if interface_device is None:
            return None
        object_type_mode, object_type_model_class = self._get_object_type_model("ipam.ipaddress")
        ip_address_object = object_type_model_class.objects.get(
            address=ip_address.get("address"),
            interface__name=interface.get("name"),
            interface__device__name=interface_device.get("name"),
            interface__device__site__name=interface_device.get("site").get("name"),
        )
        return ip_address_object

    @staticmethod
    def _get_error_response(change_set_id, error):
        """Get the error response."""
        return Response(
            {
                "change_set_id": change_set_id,
                "result": "failed",
                "errors": error,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _retrieve_assigned_object_interface_device_lookup_args(
        self, device: dict
    ) -> dict:
        """
        This method retrieves the lookup arguments for the interface device of an assigned object.

        Args:
        ----
            device (dict): A dictionary containing the details of the device. It should contain either 'id' or 'name'
                of the device and 'site' which is another dictionary containing either 'id' or 'name' of the site.

        Returns:
        -------
            dict: A dictionary containing the lookup arguments for the interface device.

        Raises:
        ------
            ValidationError: If neither 'id' nor 'name' is provided for the device or the site.

        """
        args = {}
        if device.get("id"):
            args["device__id"] = device.get("id")
        elif device.get("name"):
            args["device__name"] = device.get("name")
        else:
            raise ValidationError(
                "Interface device needs to have either id or name provided"
            )

        site = device.get("site", {})
        if site:
            if site.get("id"):
                args["device__site__id"] = site.get("id")
            elif site.get("name"):
                args["device__site__name"] = site.get("name")
            else:
                raise ValidationError(
                    "Interface device site needs to have either id or name provided"
                )
        return args

    def _handle_ipaddress_assigned_object(self, object_data: dict) -> Optional[Dict[str, Any]]:
        """Handle IPAM IP address assigned object."""
        ipaddress_assigned_object = object_data.get("assigned_object", None)

        if ipaddress_assigned_object is not None:
            assigned_object_keys = list(ipaddress_assigned_object.keys())
            model_name = assigned_object_keys[0]
            assigned_object_type = self._get_assigned_object_type(model_name)
            assigned_object_model, object_type_model_class = self._get_object_type_model(assigned_object_type)
            assigned_object_properties_dict = dict(
                ipaddress_assigned_object[model_name].items()
            )

            if len(assigned_object_properties_dict) == 0:
                return {"assigned_object": f"properties not provided for {model_name}"}

            try:
                lookups = (
                    ("device", "device__site") if model_name == "interface" else ()
                )
                args = {}

                if model_name == "interface":
                    if assigned_object_properties_dict.get("id"):
                        args["id"] = assigned_object_properties_dict.get("id")
                    elif assigned_object_properties_dict.get("name"):
                        try:
                            device = assigned_object_properties_dict.get("device", {})
                            args = self._retrieve_assigned_object_interface_device_lookup_args(
                                device
                            )
                            args["name"] = assigned_object_properties_dict.get("name")
                        except ValidationError as e:
                            return {"assigned_object": str(e)}
                    else:
                        error = f"provided properties '{assigned_object_properties_dict}' not sufficient to retrieve {model_name}"
                        return {"assigned_object": error}

                assigned_object_instance = (
                    object_type_model_class.objects.prefetch_related(*lookups).get(**args)
                )
            except object_type_model_class.DoesNotExist:
                return {
                    "assigned_object": f"Assigned object with name {ipaddress_assigned_object[model_name]} does not exist"
                }

            object_data.pop("assigned_object")
            object_data["assigned_object_type"] = assigned_object_type
            object_data["assigned_object_id"] = assigned_object_instance.id
        return None

    def _handle_interface_mac_address_compat(self,  instance, object_type: str, object_data: dict) -> Optional[Dict[str, Any]]:
        """Handle interface mac address backward compatibility."""
        # TODO(ltucker): deprecate.
        if object_type != "dcim.interface" and object_type != "virtualization.vminterface":
            return None

        if object_data.get("mac_address"):
            mac_address_value = object_data.pop("mac_address")
            mac_address_instance, _ = instance.mac_addresses.get_or_create(
                mac_address=mac_address_value,
            )
            instance.primary_mac_address = mac_address_instance
            instance.save()
        return None

    def _handle_scope(self, object_data: dict, is_nested: bool = False) -> Optional[Dict[str, Any]]:
        """Handle scope object."""
        if object_data.get("site"):
            site = object_data.pop("site")
            scope_type = "dcim.site"
            object_type_model, object_type_model_class = self._get_object_type_model(scope_type)
            # Scope type of the nested object happens to be resolved differently than in the top-level object
            # and is expected to be a content type object instead of "app_label.model_name" string format
            if is_nested:
                object_data["scope_type"] = object_type_model
            else:
                object_data["scope_type"] = scope_type
            site_id = site.get("id", None)
            if site_id is None:
                try:
                    site = object_type_model_class.objects.get(
                        name=site.get("name")
                    )
                    site_id = site.id
                except object_type_model_class.DoesNotExist:
                    return {"site": f"site with name {site.get('name')} does not exist"}

            object_data["scope_id"] = site_id

        return None

    def _transform_object_data(self, object_type: str, object_data: dict) -> Optional[Dict[str, Any]]:
        """Transform object data."""
        errors = None

        match object_type:
            case "ipam.ipaddress":
                errors = self._handle_ipaddress_assigned_object(object_data)
            case "ipam.prefix":
                errors = self._handle_scope(object_data, False)
            case "virtualization.cluster":
                errors = self._handle_scope(object_data, False)
            case "virtualization.virtualmachine":
                if cluster_object_data := object_data.get("cluster"):
                    errors = self._handle_scope(cluster_object_data, True)
                    object_data["cluster"] = cluster_object_data
            case "virtualization.vminterface":
                cluster_object_data = object_data.get("virtual_machine", {}).get("cluster")
                if cluster_object_data is not None:
                    errors = self._handle_scope(cluster_object_data, True)
                    object_data["virtual_machine"]["cluster"] = cluster_object_data
            case _:
                pass

        return errors

    def post(self, request, *args, **kwargs):
        """
        Create a new change set and apply it to the current state.

        The request body should contain a list of changes to be applied.
        """
        serializer_errors = []

        request_serializer = ApplyChangeSetRequestSerializer(data=request.data)

        change_set_id = self.request.data.get("change_set_id", None)

        if not request_serializer.is_valid():
            for field_error_name in request_serializer.errors:
                self._extract_serializer_errors(
                    field_error_name, request_serializer, serializer_errors
                )

            return self._get_error_response(change_set_id, serializer_errors)

        change_set = request_serializer.data.get("change_set", None)

        try:
            with transaction.atomic():
                for change in change_set:
                    change_id = change.get("change_id", None)
                    change_type = change.get("change_type", None)
                    object_type = change.get("object_type", None)
                    object_data = change.get("data", None)
                    object_id = change.get("object_id", None)

                    errors = self._transform_object_data(object_type, object_data)

                    if errors is not None:
                        serializer_errors.append({"change_id": change_id, **errors})
                        continue

                    serializer = self._get_serializer(change_type, object_id, object_type, object_data)

                    # Skip creating an object if it already exists
                    if change_type == "create" and serializer.context.get("pk"):
                        continue

                    if serializer.is_valid():
                        serializer.save()
                    else:
                        errors_dict = {
                            field_name: f"{field_name}: {str(field_errors[0])}"
                            for field_name, field_errors in serializer.errors.items()
                        }

                        serializer_errors.append(
                            {"change_id": change_id, **errors_dict}
                        )
                        continue

                    errors = self._handle_interface_mac_address_compat(serializer.instance, object_type, object_data)
                    if errors is not None:
                        serializer_errors.append({"change_id": change_id, **errors})
                        continue
                if len(serializer_errors) > 0:
                    raise ApplyChangeSetException
        except ApplyChangeSetException:
            return self._get_error_response(change_set_id, serializer_errors)

        data = {"change_set_id": change_set_id, "result": "success"}
        return Response(data, status=status.HTTP_200_OK)

    def _extract_serializer_errors(
        self, field_error_name, request_serializer, serializer_errors
    ):
        """Extract serializer errors."""
        if isinstance(request_serializer.errors[field_error_name], dict):
            for error_index, error_values in request_serializer.errors[
                field_error_name
            ].items():
                errors_dict = {
                    "change_id": request_serializer.data.get("change_set")[
                        error_index
                    ].get("change_id")
                }

                for field_name, field_errors in error_values.items():
                    errors_dict[field_name] = f"{str(field_errors[0])}"

                serializer_errors.append(errors_dict)
        else:
            errors = {
                field_error_name: f"{str(field_errors)}"
                for field_errors in request_serializer.errors[field_error_name]
            }

            serializer_errors.append(errors)


class ApplyChangeSetException(Exception):
    """ApplyChangeSetException used to cause atomic transaction rollback."""

    pass



def get_entity_key(model_name):
    """Get the entity key for a model name."""
    s = re.sub(r'([A-Z0-9]{2,})([A-Z])([a-z])', r'\1_\2\3', model_name)
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s)
    s = re.sub(r'_+', '_', s.lower()) # snake
    s = ''.join([word.capitalize() for word in s.split("_")]) # upperCamelCase
    return s[0].lower() + s[1:] # lowerCamelCase


class GenerateDiffView(views.APIView):
    """GenerateDiff view."""

    permission_classes = [IsAuthenticated, IsDiodeWriter]

    def post(self, request, *args, **kwargs):
        """Generate diff for entity."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            import traceback
            traceback.print_exc()
            raise

    def _post(self, request, *args, **kwargs):
        entity = request.data.get("entity")
        object_type = request.data.get("object_type")

        if not entity:
            raise ValidationError("Entity is required")
        if not object_type:
            raise ValidationError("Object type is required")

        app_label, model_name = object_type.split(".")
        model_class = apps.get_model(app_label, model_name)

        # Convert model name to lowerCamelCase for entity lookup
        entity_key = get_entity_key(model_class.__name__)
        original_entity_data = entity.get(entity_key)

        if original_entity_data is None:
            raise ValidationError(f"No data found for {entity_key} in entity got: {entity.keys()}")

        change_set = generate_changeset(original_entity_data, object_type)

        branch_id = request.headers.get("X-NetBox-Branch")

        # If branch ID is provided and branching plugin is installed, get branch name
        if branch_id and Branch is not None:
            try:
                branch = Branch.objects.get(id=branch_id)
                change_set.branch = {"id": branch.id, "name": branch.name}
            except Branch.DoesNotExist:
                logger.warning(f"Branch with ID {branch_id} does not exist")

        logger.info(f"change_set: {json.dumps(change_set.to_dict(), default=str)}")
        return Response(change_set.to_dict(), status=status.HTTP_200_OK)

# Add your plugins and plugin settings here.
# Of course uncomment this file out.

# To learn how to build images with your required plugins
# See https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins

PLUGINS = ["netbox_diode_plugin"]

# PLUGINS_CONFIG = {
#     "netbox_diode_plugin": {
#         # Diode gRPC target for communication with Diode server
#         "diode_target_override": "grpc://localhost:8080/diode",
#
#         # User allowed for Diode to NetBox communication
#         "diode_to_netbox_username": "diode-to-netbox",
#
#         # User allowed for NetBox to Diode communication
#         "netbox_to_diode_username": "netbox-to-diode",
#
#         # User allowed for data ingestion
#         "diode_username": "diode-ingestion",
#     },
# }

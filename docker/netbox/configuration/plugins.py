# Add your plugins and plugin settings here.
# Of course uncomment this file out.

# To learn how to build images with your required plugins
# See https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins

PLUGINS = ["netbox_diode_plugin"]

PLUGINS_CONFIG = {
    "netbox_diode_plugin": {
        "diode_target": "grpc://localhost:8080/diode", # The Diode gRPC target for communication with Diode server, default: "grpc://localhost:8080/diode"
        "disallow_diode_target_override": False, # Disallow the Diode target to be overridden by the user, default: False
    }
}

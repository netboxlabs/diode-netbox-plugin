# Diode NetBox Plugin

The Diode NetBox plugin is a [NetBox](https://netboxlabs.com/oss/netbox/) plugin. It is a required component of
the [Diode](https://github.com/netboxlabs/diode) ingestion service.

Diode is a NetBox ingestion service that greatly simplifies and enhances the process to add and update network data
in NetBox, ensuring your network source of truth is always accurate and can be trusted to power your network automation
pipelines.

More information about Diode can be found
at [https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/](https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/).

## Compatibility

| NetBox Version | Plugin Version |
|:--------------:|:--------------:|
|    >= 3.7.2    |     0.1.0      |
|    >= 4.1.0    |     0.4.0      |

## Installation

Source the NetBox Python virtual environment:

```shell
cd /opt/netbox
source venv/bin/activate
```

Install the plugin:

```bash
pip install netboxlabs-diode-netbox-plugin
```

In your NetBox `configuration.py` file, add `netbox_diode_plugin` to the `PLUGINS` list.

```python
PLUGINS = [
    "netbox_diode_plugin",
]
```

Also in your `configuration.py` file, in order to customise the plugin settings, add `netbox_diode_plugin`to the
`PLUGINS_CONFIG` dictionary, e.g.:

```python
PLUGINS_CONFIG = {
    "netbox_diode_plugin": {
        # Diode gRPC target for communication with Diode server
        "diode_target_override": "grpc://localhost:8080/diode",

        # User allowed for Diode to NetBox communication
        "diode_to_netbox_username": "diode-to-netbox",

        # User allowed for NetBox to Diode communication
        "netbox_to_diode_username": "netbox-to-diode",

        # User allowed for data ingestion
        "diode_username": "diode-ingestion",
    },
}
```

Note: Once you customise usernames with PLUGINS_CONFIG during first installation, you should not change or remove them
later on. Doing so will cause the plugin to stop working properly.

Restart NetBox services to load the plugin:

```
sudo systemctl restart netbox netbox-rq
```

See [NetBox Documentation](https://netboxlabs.com/docs/netbox/en/stable/plugins/#installing-plugins) for details.

## Configuration

Source the NetBox Python virtual environment (if not already):

```shell
cd /opt/netbox
source venv/bin/activate
```

Three API keys will be needed (these are random 40 character long alphanumeric strings). They can be generated and set
to the appropriate environment variables with the following commands:

```shell
# API key for the Diode service to interact with NetBox
export DIODE_TO_NETBOX_API_KEY=$(head -c20 </dev/urandom|xxd -p); env | grep DIODE_TO_NETBOX_API_KEY
# API key for the NetBox service to interact with Diode
export NETBOX_TO_DIODE_API_KEY=$(head -c20 </dev/urandom|xxd -p); env | grep NETBOX_TO_DIODE_API_KEY
# API key for Diode SDKs to ingest data into Diode
export DIODE_API_KEY=$(head -c20 </dev/urandom|xxd -p); env | grep DIODE_API_KEY
```

**Note:** store these API key strings in a safe place as they will be needed later to configure the Diode server

Run migrations to create all necessary resources:

```shell
cd /opt/netbox/netbox
./manage.py migrate netbox_diode_plugin
```

## Running Tests

```shell
make docker-compose-netbox-plugin-test
```

## License

Distributed under the PolyForm Shield License 1.0.0 License. See [LICENSE.md](./LICENSE.md) for more information.

## Required Notice

Copyright NetBox Labs, Inc.


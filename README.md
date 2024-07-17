# Diode NetBox Plugin

The Diode NetBox plugin is a [NetBox](https://netboxlabs.com/oss/netbox/) plugin and a required component of the [Diode](https://github.com/netboxlabs/diode) ingestion service.

Diode is a NetBox ingestion service that greatly simplifies and enhances the process to add and update network data
in NetBox, ensuring your network source of truth is always accurate and can be trusted to power your network automation
pipelines.

More information about Diode can be found
at [https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/](https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/).

## Compatibility

| NetBox Version | Plugin Version |
|----------------|----------------|
|   >= 3.7.2     |      0.1.0     |

## Installation

```bash
pip install netboxlabs-diode-netbox-plugin
```

In your NetBox `configuration.py` file, add `netbox_diode_plugin` to the `PLUGINS` list.

```python
PLUGINS = [
    "netbox_diode_plugin",
]
```

See [NetBox Documentation](https://netboxlabs.com/docs/netbox/en/stable/plugins/#installing-plugins) for details.

## Configuration

Source the NetBox Python virtual environment:

```shell
cd /opt/netbox
source venv/bin/activate
```

Set the following environment variables based on API keys created in your NetBox installation:

```shell
export DIODE_TO_NETBOX_API_KEY={API_KEY_1} # API key for the Diode service to interact with NetBox
export NETBOX_TO_DIODE_API_KEY={API_KEY_2} # API key for the NetBox service to interact with Diode
export INGESTION_API_KEY={API_KEY_3} # API key for Diode SDKs to ingest data into Diode
```

Note: API key values should be 40 character long alphanumeric strings

Configure the plugin:

```shell
cd /opt/netbox/netbox
./manage.py configurediodeplugin
```

## Running Tests

```shell
make docker-compose-netbox-plugin-test
```

## License

Distributed under the PolyForm Shield License 1.0.0 License. See [LICENSE.md](./LICENSE.md) for more information.

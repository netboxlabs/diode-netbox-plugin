# Diode NetBox Plugin

Diode NetBox plugin is a [NetBox](https://netboxlabs.com/oss/netbox/) plugin for the Diode ingestion service.

Diode is a new NetBox ingestion service that greatly simplifies and enhances the
process to add and update network data
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

Set following environment variables for your NetBox:

* `DIODE_TO_NETBOX_API_KEY=<API_KEY_1>` - API key for the Diode service to interact with NetBox
* `NETBOX_TO_DIODE_API_KEY=<API_KEY_2>` - API key for the NetBox service to interact with Diode
* `INGESTION_API_KEY=<API_KEY_3>` - API key for Diode SDKs to ingest data into Diode

Note: values of these environment variables should be 40 character long alphanumeric strings.

Configure the plugin by running the following command in your NetBox instance:

```shell
./manage.py configurediodeplugin
```

## Running Tests

```shell
make docker-compose-netbox-plugin-test
```

## License

Distributed under the PolyForm Shield License 1.0.0 License. See [LICENSE.md](./LICENSE.md) for more information.

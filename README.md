# Diode NetBox Plugin

## Installation

```bash
pip install netboxlabs-diode-netbox-plugin
```

In your `configuration.py` file, add `netbox_diode_plugin` to the `PLUGINS` list.

```python
PLUGINS = [
    "netbox_diode_plugin",
]
```

Ensure following environment variables are set:

```
DIODE_TO_NETBOX_API_KEY=<API_KEY_1>
NETBOX_TO_DIODE_API_KEY=<API_KEY_2>
INGESTION_API_KEY=<API_KEY_3>
```

Values of these environment variables should be 40 character long alphanumeric strings.

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

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
|    >= 4.2.3    |     1.0.0      |
|    >= 4.2.3    |     1.1.0      |
|    >= 4.2.3    |     1.2.0      |
|    >= 4.4.0    |     1.4.0      |
|    >= 4.4.0    |     1.4.1      |

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

        # Username associated with changes applied via plugin
        "diode_username": "diode",

        # netbox-to-diode client_secret created during diode bootstrap.
        "netbox_to_diode_client_secret": "..."
    },
}
```

If you are running diode locally via the quickstart, the `netbox-to-diode` client_secret may be found in `/path/to/diode/oauth2/client/client-credentials.json`. eg:
```
echo $(jq -r '.[] | select(.client_id == "netbox-to-diode") | .client_secret' /path/to/diode/oauth2/client/client-credentials.json)
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

Run migrations to create all necessary resources:

```shell
cd /opt/netbox/netbox
./manage.py migrate netbox_diode_plugin
```

## Running Tests

```shell
make docker-compose-netbox-plugin-test
```

## Generating Documentation
Generates documentation on how diode entities are matched. The generated documentation is output to [here](./docs/matching-criteria-documentation.md).
```shell
make docker-compose-generate-matching-docs
```

## License

Distributed under the NetBox Limited Use License 1.0. See [LICENSE.md](./LICENSE.md) for more information.

## Required Notice

Copyright NetBox Labs, Inc.


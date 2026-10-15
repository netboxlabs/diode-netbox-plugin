# Diode NetBox Plugin

The Diode NetBox plugin is a [NetBox](https://netboxlabs.com/oss/netbox/) plugin. It is a required component of
the [Diode](https://github.com/netboxlabs/diode) ingestion service.

Diode is a NetBox ingestion service that greatly simplifies and enhances the process to add and update network data
in NetBox, ensuring your network source of truth is always accurate and can be trusted to power your network automation
pipelines.

More information about Diode can be found
at [https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/](https://netboxlabs.com/blog/introducing-diode-streamlining-data-ingestion-in-netbox/).

## Compatibility

| NetBox Version  | Plugin Version |
|:---------------:|:--------------:|
|    >= 3.7.2     |     0.1.0      |
|    >= 4.1.0     |     0.4.0      |
|    >= 4.2.3     |     1.0.0      |
|    >= 4.2.3     |     1.1.0      |
|    >= 4.2.3     |     1.2.0      |
|    >= 4.4.0     |     1.4.0      |
|    >= 4.4.0     |     1.4.1      |
|    >= 4.4.10    |     1.7.0      |
|    >= 4.5.0     |     1.7.0      |
|    >= 4.6.0     |     1.12.0     |
|    >= 4.7.0     |     1.17.0     |

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

## Matching on unique custom fields

The plugin derives object identity from uniqueness. A custom field that is marked "must be unique" becomes an identity key, like an asset tag or a unique name. A payload that carries the value of such a field matches the one object that holds it, even when the payload carries a different name. This lets a producer key objects by an external id and rename them at the source.

The custom field key ranks below the model's own unique fields. When the matched object holds a different value for an optional unique identifier that the payload also carries, such as `asset_tag` on a device or `rd` on a VRF, the plugin refuses the match. The plan then reports an error that names the object and both values instead of an update that rewrites it. Do not mark a custom field as unique when several objects share one value, such as a stack id shared by stack members.

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


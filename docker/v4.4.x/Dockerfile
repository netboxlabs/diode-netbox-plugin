ARG NETBOX_VERSION=v4.4.10-3.4.2
FROM netboxcommunity/netbox:${NETBOX_VERSION}

COPY common/netbox/configuration/ /etc/netbox/config/
RUN chmod 755 /etc/netbox/config/* && \
    chown unit:root /etc/netbox/config/*

COPY common/netbox/local_settings.py /opt/netbox/netbox/netbox/local_settings.py
RUN chmod 755 /opt/netbox/netbox/netbox/local_settings.py && \
    chown unit:root /opt/netbox/netbox/netbox/local_settings.py

# NetBox 4.4 re-runs a Postgres table-list introspection on every ObjectType
# lookup; 4.5 caches it. Backport the cache so the 4.4 test leg is not
# dominated by that check (see the script's docstring).
COPY v4.4.x/patch-object-type-cache.py /opt/netbox/
RUN /opt/netbox/venv/bin/python /opt/netbox/patch-object-type-cache.py

COPY v4.4.x/requirements-diode-netbox-plugin.txt /opt/netbox/
ENV VIRTUAL_ENV=/opt/netbox/venv
RUN /usr/local/bin/uv pip install -r /opt/netbox/requirements-diode-netbox-plugin.txt

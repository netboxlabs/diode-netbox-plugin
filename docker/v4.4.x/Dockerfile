ARG NETBOX_VERSION=v4.4.10-3.4.2
FROM netboxcommunity/netbox:${NETBOX_VERSION}

COPY common/netbox/configuration/ /etc/netbox/config/
RUN chmod 755 /etc/netbox/config/* && \
    chown unit:root /etc/netbox/config/*

COPY common/netbox/local_settings.py /opt/netbox/netbox/netbox/local_settings.py
RUN chmod 755 /opt/netbox/netbox/netbox/local_settings.py && \
    chown unit:root /opt/netbox/netbox/netbox/local_settings.py

COPY v4.4.x/requirements-diode-netbox-plugin.txt /opt/netbox/
ENV VIRTUAL_ENV=/opt/netbox/venv
RUN /usr/local/bin/uv pip install -r /opt/netbox/requirements-diode-netbox-plugin.txt

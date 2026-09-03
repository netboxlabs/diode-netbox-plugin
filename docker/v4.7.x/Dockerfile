ARG NETBOX_VERSION=v4.7.0-5.1.0
FROM netboxcommunity/netbox:${NETBOX_VERSION}

COPY common/netbox/configuration/ /etc/netbox/config/
RUN chmod 755 /etc/netbox/config/* && \
    chown netbox:root /etc/netbox/config/*

COPY common/netbox/local_settings.py /opt/netbox/netbox/netbox/local_settings.py
RUN chmod 755 /opt/netbox/netbox/netbox/local_settings.py && \
    chown netbox:root /opt/netbox/netbox/netbox/local_settings.py

COPY v4.7.x/requirements-diode-netbox-plugin.txt /opt/netbox/
ENV VIRTUAL_ENV=/opt/netbox/venv
RUN /usr/local/bin/uv pip install -r /opt/netbox/requirements-diode-netbox-plugin.txt

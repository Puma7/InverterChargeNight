"""Select an isolated loopback address without touching a running laboratory."""
from ipaddress import IPv4Address
import os


def inverter_host():
    host = os.environ.get("ICN_LAB_INVERTER_HOST", "127.0.0.1")
    if not IPv4Address(host).is_loopback:
        raise ValueError("ICN_LAB_INVERTER_HOST must be an IPv4 loopback address")
    return host

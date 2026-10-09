"""Whether a screen is on, and switching it.

On and off are not symmetric. Off is a command to a device that is answering. On has nobody to
talk to, so it is a wake-on-LAN packet, and it only works when the set was told to listen for one
("Power on with mobile" on Samsung sets). A device that cannot be woken this way is reported as
refused, not as failed.
"""
from __future__ import annotations

import socket
from collections.abc import Mapping

from . import androidtv, samsung
from .config import Device


def state(dev: Device, on_their_network: bool = True, *, env: Mapping[str, str] | None = None) -> bool | None:
    """True on, False off, None when nobody can tell.

    Pass on_their_network=False when this machine may not have a route to the device (a laptop on
    another network). A set that is on but out of reach reads exactly like a set that is off.
    """
    if not on_their_network:
        return None
    if dev.kind == "androidtv":
        return androidtv.alive(dev, env=env)
    host, port = dev.host, dev.api_port
    if samsung.reachable(host, port):
        return True
    # One refused connection is not a verdict, so ask again with a longer wait.
    return samsung.reachable(host, port, timeout=3.0)


def wake_packet(mac: str) -> bytes:
    """The magic packet, six 0xFF bytes and then the address sixteen times."""
    clean = mac.replace(":", "").replace("-", "").strip()
    if len(clean) != 12:
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(clean) * 16


def send_wol(mac: str, broadcast: str = "255.255.255.255") -> None:
    pkt = wake_packet(mac)
    for port in (9, 7):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(pkt, (broadcast, port))


def power_on(dev: Device) -> tuple[str, str]:
    if not dev.mac:
        return "refused", f"{dev.name} has no mac in the config, so it cannot be woken over the network"
    try:
        send_wol(dev.mac)
    except (OSError, ValueError) as e:
        return "failure", f"could not send the wake packet: {e}"
    return "success", "wake packet sent (the set wakes if it listens for one)"


def power_off(dev: Device, token_file, *, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Switch off, after checking the device is on right now.

    The state is read again at the moment of acting, because on these sets a power key sent to a
    device that is already off can switch it on.
    """
    if state(dev, env=env) is not True:
        return "refused", f"{dev.name} is not answering, so it is already off"
    if dev.kind == "androidtv":
        androidtv.connect(dev.adb, env=env)
        ok, why = androidtv.power_key(dev, env=env)
    else:
        ok, why = samsung.send_key(dev, token_file, "KEY_POWER")
    return ("success" if ok else "failure"), why

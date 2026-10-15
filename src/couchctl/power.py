"""Whether a screen is on, and switching it.

On and off are not symmetric. Off is a command to a device that is answering. On has nobody to
talk to, so it is a wake-on-LAN packet, and it only works when the set was told to listen for one
("Power on with mobile" on Samsung sets). A device that cannot be woken this way is reported as
refused, not as failed.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Mapping

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
    # One refused connection is not a verdict, so ask again with a longer wait.
    if not (samsung.reachable(host, port) or samsung.reachable(host, port, timeout=3.0)):
        return False
    # Answering is not the same as on. A Samsung set keeps its network up in standby and while it
    # shuts down, and says which in PowerState. Read as on, a set in standby made a wake report
    # success with the screen dark, and made power_off send the power key, which switched it on.
    said = samsung.info(dev)
    if not said:
        return None                                   # it answers but does not describe itself
    return said.get("PowerState", "on") == "on"       # a set too old to report it is on when it answers


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


#: How long power_on keeps waking a device before it reports that the device did not come on. A set
#: switched off a moment ago is still shutting down for several seconds and ignores a packet sent
#: then, so one packet is not enough.
WAKE_WAIT = 25.0
#: How often the packet is sent again while waiting.
WAKE_EVERY = 2.0


def broadcasts(host: str | None) -> list[str]:
    """Where a wake packet is sent: the local network's broadcast address, and also the device's own
    /24 broadcast address when its host is an IPv4 address. Some systems send 255.255.255.255 out of
    one network interface only, and the directed address reaches a device on another one."""
    out = ["255.255.255.255"]
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return out
    if ip.version == 4:
        out.append(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
    return out


def wake(dev: Device, *, wait: float = WAKE_WAIT, every: float = WAKE_EVERY,
         env: Mapping[str, str] | None = None,
         send: Callable[[str, str], None] = send_wol, is_on: Callable[[], bool] | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[str, str]:
    """Wake a device over the network and check that it came on.

    The wake packet is sent every `every` seconds until the device is on or `wait` seconds pass.
    The outcome is "success" only when the device is on (a Samsung set that answers in standby is
    not), "failure" when it never came on, and "refused" when the device has no MAC address to wake.
    """
    if not dev.mac:
        return "refused", f"{dev.name} has no mac in the config, so it cannot be woken over the network"
    check = is_on or (lambda: state(dev, env=env) is True)
    started, sent = clock(), 0
    while True:
        try:
            for address in broadcasts(dev.host):
                send(dev.mac, address)
        except (OSError, ValueError) as e:
            return "failure", f"could not send the wake packet: {e}"
        sent += 1
        sleep(every)
        if check():
            return "success", f"{dev.name} is on, after {clock() - started:.0f} s"
        if clock() - started >= wait:
            return "failure", (f"{dev.name} did not come on after {sent} wake packets over {wait:.0f} s. "
                               "A set wakes from the network only when it listens for it (Power on with mobile "
                               "on Samsung sets), and one switched off moments ago may still be shutting down.")


def power_on(dev: Device, *, wait: float = WAKE_WAIT, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Wake a device and report whether it came on (see wake)."""
    return wake(dev, wait=wait, env=env)


def power_off(dev: Device, token_file, *, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Switch off, after checking the device is on right now.

    The state is read again at the moment of acting, because on these sets a power key sent to a
    device that is already off, or in standby, switches it on.
    """
    now = state(dev, env=env)
    if now is None:
        return "refused", (f"{dev.name} answers but does not say whether it is on, so the power key, "
                           "which switches it either way, was not sent")
    if now is not True:
        return "refused", f"{dev.name} is not on (not answering, or in standby), so it is already off"
    if dev.kind == "androidtv":
        androidtv.connect(dev.adb, env=env)
        ok, why = androidtv.power_key(dev, env=env)
    else:
        ok, why = samsung.send_key(dev, token_file, "KEY_POWER")
    return ("success" if ok else "failure"), why

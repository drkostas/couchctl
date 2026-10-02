"""Samsung Tizen televisions over the local network.

Port 8001 answers plain HTTP without authentication. `/api/v2/` describes the set, and
`/api/v2/applications/<id>` reports whether an app is running and visible (GET) or launches it
(POST). The remote-control channel is a websocket on 8002. Sets that support token auth show a
prompt on screen the first time a client connects, and hand back a token once somebody allows it.

A set in standby leaves the network, so "not answering" usually means "off". Callers must still
treat it as unknown when they cannot be sure they are on the same network as the set.
"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import ssl
import urllib.request

from . import tokens as token_store
from .config import Device

CLIENT_NAME = "couchctl"


def reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def info(dev: Device, timeout: float = 4.0) -> dict:
    """The set's own description (model, name, wifiMac, TokenAuthSupport), or {} when it is not answering."""
    try:
        with urllib.request.urlopen(f"http://{dev.host}:{dev.api_port}/api/v2/", timeout=timeout) as r:
            return (json.loads(r.read() or b"{}") or {}).get("device") or {}
    except Exception:  # noqa: BLE001 - a set that does not answer is an answer
        return {}


def app_state(dev: Device, app_id: str, timeout: float = 4.0) -> tuple[bool | None, bool | None]:
    """(running, visible) for one app, or (None, None) when the set did not answer."""
    try:
        with urllib.request.urlopen(f"http://{dev.host}:{dev.api_port}/api/v2/applications/{app_id}", timeout=timeout) as r:
            d = json.loads(r.read() or b"{}") or {}
        return bool(d.get("running")), bool(d.get("visible"))
    except Exception:  # noqa: BLE001
        return None, None


def foreground(dev: Device, among: list[str]) -> tuple[bool, str | None]:
    """(could the set be reached, which of `among` is on screen).

    None for the app means none of `among` is visible, not that nothing is. The home screen and
    any app outside the list read the same way.
    """
    reached = False
    for app_id in among:
        running, visible = app_state(dev, app_id)
        if running is not None:
            reached = True
            if visible:
                return True, app_id
    return reached, None


def launch(dev: Device, app_id: str, timeout: float = 10.0) -> tuple[bool, str]:
    req = urllib.request.Request(f"http://{dev.host}:{dev.api_port}/api/v2/applications/{app_id}", method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def remote_url(dev: Device, token: str | None) -> str:
    name = base64.b64encode(CLIENT_NAME.encode()).decode()
    url = f"{dev.remote_scheme}://{dev.host}:{dev.remote_port}/api/v2/channels/samsung.remote.control?name={name}"
    return f"{url}&token={token}" if token else url


async def _talk(dev: Device, token: str | None, payload: dict | None, timeout: float = 30.0):
    """Connect, read the greeting, optionally send one command. Returns (greeting, new token)."""
    from websockets.asyncio.client import connect

    ctx = None
    if dev.remote_scheme == "wss":
        # A television signs its own certificate, so it cannot be verified. Verification is off for
        # this socket only.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    async with connect(remote_url(dev, token), ssl=ctx, open_timeout=timeout, close_timeout=5, max_size=2**20) as ws:
        greeting = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        fresh = (greeting.get("data") or {}).get("token")
        if payload is not None:
            await ws.send(json.dumps(payload))
            await asyncio.sleep(1.0)  # give the set a moment to act before the socket closes
        return greeting, fresh


def pair(dev: Device, token_file, timeout: float = 40.0) -> tuple[str, str]:
    """Ask the set for a token. It shows a prompt, and somebody in the room has to allow it.

    Returns (outcome, message), where outcome is "success", "refused" or "failure".
    """
    if token_store.read(token_file, dev.name):
        return "success", "already paired"
    try:
        greeting, fresh = asyncio.run(_talk(dev, None, None, timeout))
    except Exception as e:  # noqa: BLE001
        return "failure", f"{type(e).__name__}: {str(e)[:140]}"
    if greeting.get("event") == "ms.channel.unauthorized":
        return "refused", "the prompt on the television was declined"
    if not fresh:
        return "refused", "the television offered no token (the prompt was declined or timed out)"
    token_store.write(token_file, dev.name, fresh)
    return "success", "paired"


def send_key(dev: Device, token_file, key: str) -> tuple[bool, str]:
    """Press one remote key (KEY_POWER, KEY_HOME, KEY_VOLUP and so on)."""
    said = info(dev)
    if not said:
        return False, "the set is not answering, so there is nothing to talk to"
    token = token_store.read(token_file, dev.name)
    if said.get("TokenAuthSupport") == "true" and not token:
        return False, f"not paired: run `couchctl pair {dev.name}` once and allow the prompt on the television"
    payload = {
        "method": "ms.remote.control",
        "params": {"Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"},
    }
    try:
        greeting, fresh = asyncio.run(_talk(dev, token, payload))
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:140]}"
    if greeting.get("event") == "ms.channel.unauthorized":
        return False, f"the television refused the token: run `couchctl pair {dev.name} --again`"
    if fresh and fresh != token:
        token_store.write(token_file, dev.name, fresh)
    return True, f"{key} sent"


def paired_and_accepted(dev: Device, token_file) -> bool | None:
    """True when the held token still opens the remote channel, False when it does not, None when the set is off."""
    token = token_store.read(token_file, dev.name)
    if not token:
        return False
    if not reachable(dev.host, dev.api_port):
        return None
    try:
        greeting, _ = asyncio.run(_talk(dev, token, None, timeout=20))
    except Exception:  # noqa: BLE001
        return False
    return greeting.get("event") == "ms.channel.connect"

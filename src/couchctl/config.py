"""The config file: which screens exist, how to reach them, and which buttons to redirect.

    token_file = "~/.config/couchctl/tokens.json"   # optional

    [devices.living-room]
    kind = "samsung"                 # "samsung" (Tizen) or "androidtv"
    host = "192.168.1.20"
    mac = "aa:bb:cc:dd:ee:ff"        # optional, for power on by wake-on-LAN

    [devices.projector]
    kind = "androidtv"
    adb = "192.168.1.30:5555"        # how adb reaches it

    [[devices.living-room.redirect]]
    from = "3201910019365"           # the app the button opens
    to = "3201606009684"             # the app to open instead
    name = "Prime Video button to Spotify"   # optional, for logs
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

KINDS = ("samsung", "androidtv")
DEFAULT_TOKENS = "~/.config/couchctl/tokens.json"


@dataclass
class Redirect:
    source: str
    target: str
    name: str = ""

    def label(self) -> str:
        return self.name or f"{self.source} to {self.target}"


@dataclass
class Device:
    name: str
    kind: str
    host: str = ""
    adb: str = ""
    mac: str = ""
    # The Samsung remote channel. Real sets use wss on 8002 with a self-signed certificate.
    remote_scheme: str = "wss"
    remote_port: int = 8002
    api_port: int = 8001
    redirects: list[Redirect] = field(default_factory=list)

    def redirect_for(self, app_id: str | None) -> Redirect | None:
        return next((r for r in self.redirects if r.source == app_id), None)


@dataclass
class Config:
    devices: dict[str, Device]
    token_file: Path

    def device(self, name: str) -> Device:
        if name not in self.devices:
            known = ", ".join(sorted(self.devices)) or "none"
            raise KeyError(f"no device called {name!r} in the config (known: {known})")
        return self.devices[name]


def parse(data: dict) -> Config:
    problems: list[str] = []
    devices: dict[str, Device] = {}
    for name, d in (data.get("devices") or {}).items():
        kind = d.get("kind")
        if kind not in KINDS:
            problems.append(f"devices.{name}.kind must be one of {', '.join(KINDS)}")
            continue
        if kind == "samsung" and not d.get("host"):
            problems.append(f"devices.{name}.host is required for a samsung device")
        if kind == "androidtv" and not (d.get("adb") or d.get("host")):
            problems.append(f"devices.{name} needs adb (host:port) or host for an androidtv device")
        redirects = []
        for i, r in enumerate(d.get("redirect") or []):
            if not r.get("from") or not r.get("to"):
                problems.append(f"devices.{name}.redirect[{i}] needs both from and to")
                continue
            redirects.append(Redirect(str(r["from"]), str(r["to"]), str(r.get("name", ""))))
        adb = d.get("adb") or (f"{d['host']}:5555" if kind == "androidtv" and d.get("host") else "")
        devices[name] = Device(
            name=name, kind=kind, host=d.get("host", ""), adb=adb, mac=d.get("mac", ""),
            remote_scheme=d.get("remote_scheme", "wss"), remote_port=int(d.get("remote_port", 8002)),
            api_port=int(d.get("api_port", 8001)), redirects=redirects,
        )
    if problems:
        raise ValueError("invalid config:\n  - " + "\n  - ".join(problems))
    token_file = Path(os.path.expanduser(data.get("token_file") or DEFAULT_TOKENS))
    return Config(devices=devices, token_file=token_file)


def load(path: str | os.PathLike) -> Config:
    with open(path, "rb") as f:
        return parse(tomllib.load(f))


def default_path() -> Path:
    return Path(os.environ.get("COUCHCTL_CONFIG") or os.path.expanduser("~/.config/couchctl/config.toml"))

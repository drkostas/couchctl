"""Philips Hue lights, through the bridge's local API (CLIP v2).

The bridge answers on the local network over HTTPS. Reading and changing lights needs an
application key, which the bridge hands out only within about 30 seconds of its round link button
being pressed: that press is the permission, and nothing can take a key without it.

    keys = pair("192.0.2.40")                      # press the link button first, or within `wait`
    bridge = Bridge("192.0.2.40", keys["key"])
    bridge.lights()                                # [{"id", "name", "on", "brightness", ...}]
    bridge.set_light("Desk lamp", on=True, brightness=40, color="#ff8800")
    bridge.set_group("Living room", on=False)
    bridge.recall_scene("Relax", group="Living room")

What comes back is a plain summary of the bridge's own resources:

- a light: its id, name, whether it is on, its brightness (0 to 100, or None for a plug that only
  switches), whether it takes colors and color temperatures, and whether the bridge can reach it
  (None when the bridge does not say);
- a group: a room or a zone, with the id of the grouped light that switches all of it at once and
  the ids of its lights;
- a scene: its id, name and the group it belongs to.

The key is a secret: whoever holds it can switch every light. Keep it where you keep passwords.

The discovery service is reached over fully verified HTTPS. A bridge's own certificate is signed
by one of the vendor's Hue root CAs, with the bridge id as its name rather than the address, so the
connection to a bridge is verified against those roots (its name is not checked, since we dial an
address and the certificate names the bridge id, and a certificate signed by a Hue root is proof of
a genuine bridge). An old bridge that serves a self-signed certificate needs `insecure=True`, which
turns the check off and sends the key to whatever answers, so use it only on a network you trust.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

#: the vendor's discovery service: the bridges seen from this network's public address
DISCOVERY_URL = "https://discovery.meethue.com/"
#: how long a pair attempt keeps asking for a key, in seconds. The bridge accepts a request for
#: about 30 seconds after its button is pressed, so asking for longer lets the person press it after
#: starting the command.
PAIR_WAIT = 30.0
#: the bridge's error type for "the link button was not pressed"
LINK_BUTTON_NOT_PRESSED = 101

#: (method, url, body or None, headers, timeout) -> (status, body). Tests pass a fake bridge.
Transport = Callable[[str, str, "bytes | None", dict, float], "tuple[int, bytes]"]


#: the vendor's own root certificate authorities, which sign every genuine Hue bridge's local
#: certificate: "root-bridge" (Philips Hue, 2017 to 2038) and "Hue Root CA 01" (Signify Hue, from
#: 2025). A bridge call is verified against these, so the application key goes only to a real bridge.
_HUE_ROOT_CA = """\
-----BEGIN CERTIFICATE-----
MIICMjCCAdigAwIBAgIUO7FSLbaxikuXAljzVaurLXWmFw4wCgYIKoZIzj0EAwIw
OTELMAkGA1UEBhMCTkwxFDASBgNVBAoMC1BoaWxpcHMgSHVlMRQwEgYDVQQDDAty
b290LWJyaWRnZTAiGA8yMDE3MDEwMTAwMDAwMFoYDzIwMzgwMTE5MDMxNDA3WjA5
MQswCQYDVQQGEwJOTDEUMBIGA1UECgwLUGhpbGlwcyBIdWUxFDASBgNVBAMMC3Jv
b3QtYnJpZGdlMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEjNw2tx2AplOf9x86
aTdvEcL1FU65QDxziKvBpW9XXSIcibAeQiKxegpq8Exbr9v6LBnYbna2VcaK0G22
jOKkTqOBuTCBtjAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNV
HQ4EFgQUZ2ONTFrDT6o8ItRnKfqWKnHFGmQwdAYDVR0jBG0wa4AUZ2ONTFrDT6o8
ItRnKfqWKnHFGmShPaQ7MDkxCzAJBgNVBAYTAk5MMRQwEgYDVQQKDAtQaGlsaXBz
IEh1ZTEUMBIGA1UEAwwLcm9vdC1icmlkZ2WCFDuxUi22sYpLlwJY81Wrqy11phcO
MAoGCCqGSM49BAMCA0gAMEUCIEBYYEOsa07TH7E5MJnGw557lVkORgit2Rm1h3B2
sFgDAiEA1Fj/C3AN5psFMjo0//mrQebo0eKd3aWRx+pQY08mk48=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIBzDCCAXOgAwIBAgICEAAwCgYIKoZIzj0EAwIwPDELMAkGA1UEBhMCTkwxFDAS
BgNVBAoMC1NpZ25pZnkgSHVlMRcwFQYDVQQDDA5IdWUgUm9vdCBDQSAwMTAgFw0y
NTAyMjUwMDAwMDBaGA8yMDUwMTIzMTIzNTk1OVowPDELMAkGA1UEBhMCTkwxFDAS
BgNVBAoMC1NpZ25pZnkgSHVlMRcwFQYDVQQDDA5IdWUgUm9vdCBDQSAwMTBZMBMG
ByqGSM49AgEGCCqGSM49AwEHA0IABFfOO0jfSAUXGQ9kjEDzyBrcMQ3ItyA5krE+
cyvb1Y3xFti7KlAad8UOnAx0FBLn7HZrlmIwm1QnX0fK3LPM13mjYzBhMB0GA1Ud
DgQWBBTF1pSpsCASX/z0VHLigxU2CAaqoTAfBgNVHSMEGDAWgBTF1pSpsCASX/z0
VHLigxU2CAaqoTAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAKBggq
hkjOPQQDAgNHADBEAiAk7duT+IHbOGO4UUuGLAEpyYejGZK9Z7V9oSfnvuQ5BQIg
IYSgwwxHXm73/JgcU9lAM6c8Bmu3UE3kBIUwBs1qXFw=
-----END CERTIFICATE-----
"""


def _discovery_context() -> ssl.SSLContext:
    """discovery.meethue.com is a public service with an ordinary certificate, so verify it fully."""
    return ssl.create_default_context()


def _bridge_context() -> ssl.SSLContext:
    """Verify a bridge's certificate against the Hue root CAs. Hostname checking stays off, because
    the certificate names the bridge id and we dial an address: a certificate that chains to a Hue
    root is itself proof of a genuine bridge."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=_HUE_ROOT_CA)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _insecure_context() -> ssl.SSLContext:
    """No certificate check at all, for an old bridge that serves a self-signed certificate and only
    when the caller opts in. The key is then sent to whatever answers, so trust the network first."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _context_for(url: str) -> ssl.SSLContext:
    host = urllib.parse.urlsplit(url).hostname or ""
    return _discovery_context() if host == "discovery.meethue.com" else _bridge_context()


def _open(url: str, ctx: ssl.SSLContext, method: str, body: bytes | None, headers: dict,
          timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _https(method: str, url: str, body: bytes | None, headers: dict, timeout: float) -> tuple[int, bytes]:
    """Verifying transport: the discovery service against the public CAs, a bridge against the Hue
    roots."""
    return _open(url, _context_for(url), method, body, headers, timeout)


def _https_insecure(method: str, url: str, body: bytes | None, headers: dict, timeout: float) -> tuple[int, bytes]:
    """Opt-in transport for an old self-signed bridge: no certificate check (discovery is never
    reached through this)."""
    return _open(url, _insecure_context(), method, body, headers, timeout)


def _transport_for(transport: "Transport | None", insecure: bool) -> "Transport":
    """A caller's own transport if given, else the verifying one, or the insecure one on opt-in."""
    return transport or (_https_insecure if insecure else _https)


def _json(raw: bytes):
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return None


class HueError(Exception):
    """The bridge refused a request, or did not answer. The message is the bridge's own words."""


def discover(timeout: float = 6.0, transport: Transport | None = None) -> list[dict]:
    """The bridges the vendor's discovery service has seen from this network: [{"id", "host"}].

    The service lists bridges that have reported in from the same public address, so it finds a
    bridge on a home network without any multicast. It needs internet access, and it can list a
    bridge a few minutes late after the bridge first comes online. An empty list means none was
    found, not that none exists."""
    status, raw = (transport or _https)("GET", DISCOVERY_URL, None, {}, timeout)
    found = _json(raw) if status == 200 else None
    return [{"id": b.get("id", ""), "host": b.get("internalipaddress", "")}
            for b in (found or []) if isinstance(b, dict) and b.get("internalipaddress")]


def config(host: str, timeout: float = 6.0, transport: Transport | None = None, *,
           insecure: bool = False) -> dict:
    """What a bridge says about itself without a key (name, model, bridge id, software version),
    or {} when nothing answered as a bridge there. `insecure=True` skips the certificate check for
    an old self-signed bridge."""
    try:
        status, raw = _transport_for(transport, insecure)("GET", f"https://{host}/api/0/config", None, {}, timeout)
    except OSError:
        return {}
    said = _json(raw) if status == 200 else None
    return said if isinstance(said, dict) and said.get("bridgeid") else {}


def pair(host: str, app: str = "couchctl#device", *, wait: float = PAIR_WAIT, every: float = 2.0,
         transport: Transport | None = None, insecure: bool = False,
         sleep=time.sleep, clock=time.monotonic) -> dict:
    """Ask the bridge for an application key, until its link button is pressed or `wait` passes.

    Returns {"outcome": "success", "key": ..., "clientkey": ...} once the bridge hands a key over,
    {"outcome": "refused", "why": ...} when nobody pressed the button in time, and
    {"outcome": "failure", "why": ...} when the bridge did not answer or refused for another reason.
    `app` is the name the bridge lists the key under ("application#device", at most 40 characters).
    The key is never printed or logged here; the caller keeps it."""
    send = _transport_for(transport, insecure)
    body = json.dumps({"devicetype": app[:40], "generateclientkey": True}).encode()
    started, last = clock(), "the bridge did not answer"
    while True:
        try:
            status, raw = send("POST", f"https://{host}/api", body, {"Content-Type": "application/json"}, 8.0)
            said = _json(raw)
            if not isinstance(said, list) or not said:
                last = f"the bridge answered HTTP {status} with no result"
            elif "success" in said[0]:
                ok = said[0]["success"]
                return {"outcome": "success", "key": ok.get("username", ""), "clientkey": ok.get("clientkey", "")}
            else:
                err = said[0].get("error") or {}
                if err.get("type") != LINK_BUTTON_NOT_PRESSED:
                    return {"outcome": "failure", "why": f"the bridge refused: {err.get('description', 'no reason given')}"}
                last = "the link button was not pressed"
        except OSError as e:
            last = f"the bridge did not answer ({type(e).__name__})"
        if clock() - started + every > wait:
            break
        sleep(every)
    if last == "the link button was not pressed":
        return {"outcome": "refused", "why": f"the link button on the bridge was not pressed within {wait:.0f} s"}
    return {"outcome": "failure", "why": last}


def hex_to_xy(color: str) -> tuple[float, float]:
    """An sRGB color ("#ff8800") as the CIE xy point the bridge takes. The bridge moves the point
    into the range each light can show, so any color is accepted."""
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a color in #rrggbb form: {color!r}")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in rgb)
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    if total == 0:
        return 0.3127, 0.3290       # black: keep the white point; brightness decides what is seen
    return round(x / total, 4), round(y / total, 4)


def kelvin_to_mirek(kelvin: int) -> int:
    """A white's color temperature in kelvin as the bridge's mirek, within the 153 to 500 it takes."""
    return max(153, min(500, round(1_000_000 / kelvin)))


def _state(on=None, brightness=None, color=None, kelvin=None, transition_ms=None) -> dict:
    body: dict = {}
    if on is not None:
        body["on"] = {"on": bool(on)}
    if brightness is not None:
        b = max(0.0, min(100.0, float(brightness)))
        if b == 0:
            body["on"] = {"on": False}
        else:
            body["dimming"] = {"brightness": b}
            body.setdefault("on", {"on": True})
    if color is not None:
        x, y = hex_to_xy(color)
        body["color"] = {"xy": {"x": x, "y": y}}
    if kelvin is not None:
        body["color_temperature"] = {"mirek": kelvin_to_mirek(int(kelvin))}
    if transition_ms is not None:
        body["dynamics"] = {"duration": int(transition_ms)}
    if not body:
        raise ValueError("nothing to change: give on, brightness, color or kelvin")
    return body


class Bridge:
    """One bridge, reached at `host` with an application key from pair()."""

    def __init__(self, host: str, key: str, *, timeout: float = 8.0, transport: Transport | None = None,
                 insecure: bool = False):
        self.host, self._key, self.timeout = host, key, timeout
        self._send = _transport_for(transport, insecure)

    def __repr__(self) -> str:      # the key stays out of logs and tracebacks
        return f"Bridge({self.host!r})"

    def _call(self, method: str, path: str, body: dict | None = None) -> list:
        headers = {"hue-application-key": self._key}
        raw_body = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw_body = json.dumps(body).encode()
        try:
            status, raw = self._send(method, f"https://{self.host}/clip/v2/{path}", raw_body, headers, self.timeout)
        except OSError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise HueError(f"the bridge at {self.host} did not present a trusted Hue certificate. "
                               "If it is an older bridge with a self-signed certificate, make the "
                               "Bridge with insecure=True.") from None
            raise HueError(f"the bridge at {self.host} did not answer ({type(e).__name__})") from None
        said = _json(raw)
        errors = [e.get("description", "") for e in (said or {}).get("errors") or []] if isinstance(said, dict) else []
        if status == 403 or status == 401:
            raise HueError("the bridge does not accept this key; pair again")
        if status >= 300 or errors:
            raise HueError("; ".join(e for e in errors if e) or f"the bridge answered HTTP {status}")
        return (said or {}).get("data") or []

    def resources(self, rtype: str) -> list[dict]:
        """The bridge's own resources of one type ("light", "room", "zone", "scene", "device", ...)."""
        return self._call("GET", f"resource/{rtype}")

    # ---------------------------------------------------------------- reading

    def lights(self) -> list[dict]:
        reach = {c.get("owner", {}).get("rid"): c.get("status") == "connected"
                 for c in self.resources("zigbee_connectivity")}
        out = []
        for light in self.resources("light"):
            device = (light.get("owner") or {}).get("rid")
            out.append({
                "id": light["id"],
                "name": (light.get("metadata") or {}).get("name", ""),
                "archetype": (light.get("metadata") or {}).get("archetype", ""),
                "on": bool((light.get("on") or {}).get("on")),
                "brightness": (light.get("dimming") or {}).get("brightness"),
                "color": "color" in light,
                "temperature": "color_temperature" in light,
                "reachable": reach.get(device),
                "device": device,
            })
        return out

    def groups(self) -> list[dict]:
        """Rooms and zones. A room holds devices and a zone holds lights; both come back as the ids
        of their lights, with the grouped light that switches them all at once."""
        light_of_device: dict[str, list[str]] = {}
        for light in self.resources("light"):
            light_of_device.setdefault((light.get("owner") or {}).get("rid"), []).append(light["id"])
        out = []
        for kind in ("room", "zone"):
            for g in self.resources(kind):
                lights: list[str] = []
                for child in g.get("children") or []:
                    if child.get("rtype") == "light":
                        lights.append(child["rid"])
                    elif child.get("rtype") == "device":
                        lights += light_of_device.get(child["rid"], [])
                grouped = next((s["rid"] for s in g.get("services") or [] if s.get("rtype") == "grouped_light"), None)
                out.append({"id": g["id"], "kind": kind, "name": (g.get("metadata") or {}).get("name", ""),
                            "grouped_light": grouped, "lights": lights})
        return out

    def scenes(self) -> list[dict]:
        return [{"id": s["id"], "name": (s.get("metadata") or {}).get("name", ""),
                 "group": (s.get("group") or {}).get("rid"), "group_kind": (s.get("group") or {}).get("rtype")}
                for s in self.resources("scene")]

    # ---------------------------------------------------------------- finding by id or name

    @staticmethod
    def _one(items: list[dict], ref: str, what: str) -> dict:
        hit = [i for i in items if i["id"] == ref] or [i for i in items if i["name"].casefold() == ref.casefold()]
        if len(hit) == 1:
            return hit[0]
        names = ", ".join(sorted({i["name"] for i in items})) or "none"
        if not hit:
            raise HueError(f"no {what} called {ref!r} on the bridge (known: {names})")
        raise HueError(f"{len(hit)} {what}s are called {ref!r}; use the id")

    def light(self, ref: str) -> dict:
        return self._one(self.lights(), ref, "light")

    def group(self, ref: str) -> dict:
        return self._one(self.groups(), ref, "room or zone")

    # ---------------------------------------------------------------- changing

    def set_light(self, ref: str, *, on=None, brightness=None, color=None, kelvin=None, transition_ms=None) -> dict:
        """Change one light, by id or name. Brightness is 0 to 100 (0 switches it off), color is
        "#rrggbb", kelvin a white's temperature. Returns the light as it was found before."""
        light = self.light(ref)
        self._call("PUT", f"resource/light/{light['id']}", _state(on, brightness, color, kelvin, transition_ms))
        return light

    def set_group(self, ref: str, *, on=None, brightness=None, color=None, kelvin=None, transition_ms=None) -> dict:
        """Change every light of a room or zone at once, by id or name, through its grouped light."""
        group = self.group(ref)
        if not group["grouped_light"]:
            raise HueError(f"{group['name']} has no grouped light to switch")
        self._call("PUT", f"resource/grouped_light/{group['grouped_light']}",
                   _state(on, brightness, color, kelvin, transition_ms))
        return group

    def recall_scene(self, ref: str, group: str | None = None) -> dict:
        """Show a scene, by id or name. Scenes of the same name exist in several rooms, so `group`
        (a room or zone, by id or name) says whose is meant."""
        scenes = self.scenes()
        if group is not None:
            gid = self.group(group)["id"]
            scenes = [s for s in scenes if s["group"] == gid]
        scene = self._one(scenes, ref, "scene")
        self._call("PUT", f"resource/scene/{scene['id']}", {"recall": {"action": "active"}})
        return scene

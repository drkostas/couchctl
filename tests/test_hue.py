"""Hue: pairing waits for the link button, reading summarises the bridge, changes reach the right resource."""
import json

import pytest

from couchctl import hue
from couchctl.cli import main

KEY = "test-application-key"


class FakeBridge:
    """A bridge's local API, answering the requests couchctl makes."""

    def __init__(self, presses_after: int = 0):
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.presses_after = presses_after
        self.res = {
            "light": [
                {"id": "l1", "owner": {"rid": "d1"}, "metadata": {"name": "Desk lamp", "archetype": "classic_bulb"},
                 "on": {"on": True}, "dimming": {"brightness": 80.0}, "color": {}, "color_temperature": {}},
                {"id": "l2", "owner": {"rid": "d2"}, "metadata": {"name": "Plant plug", "archetype": "plug"},
                 "on": {"on": False}},
                {"id": "l3", "owner": {"rid": "d3"}, "metadata": {"name": "Strip", "archetype": "hue_lightstrip"},
                 "on": {"on": False}, "dimming": {"brightness": 10.0}, "color": {}},
            ],
            "zigbee_connectivity": [
                {"id": "z1", "owner": {"rid": "d1"}, "status": "connected"},
                {"id": "z2", "owner": {"rid": "d2"}, "status": "connectivity_issue"},
            ],
            "room": [{"id": "r1", "metadata": {"name": "Living room"},
                      "children": [{"rid": "d1", "rtype": "device"}, {"rid": "d2", "rtype": "device"}],
                      "services": [{"rid": "g1", "rtype": "grouped_light"}]}],
            "zone": [{"id": "zn1", "metadata": {"name": "Top lights"},
                      "children": [{"rid": "l3", "rtype": "light"}],
                      "services": [{"rid": "g2", "rtype": "grouped_light"}]}],
            "scene": [{"id": "s1", "metadata": {"name": "Relax"}, "group": {"rid": "r1", "rtype": "room"}},
                      {"id": "s2", "metadata": {"name": "Relax"}, "group": {"rid": "zn1", "rtype": "zone"}},
                      {"id": "s3", "metadata": {"name": "Read"}, "group": {"rid": "r1", "rtype": "room"}}],
        }

    def __call__(self, method, url, body, headers, timeout):
        sent = json.loads(body) if body else None
        self.calls.append((method, url, sent, headers))
        path = url.split("://", 1)[1].split("/", 1)[1]
        if path == "api" and method == "POST":
            if self.presses_after > 0:
                self.presses_after -= 1
                return 200, json.dumps([{"error": {"type": 101, "address": "", "description": "link button not pressed"}}]).encode()
            return 200, json.dumps([{"success": {"username": KEY, "clientkey": "CK"}}]).encode()
        if path == "api/0/config":
            return 200, json.dumps({"name": "Hue Bridge", "bridgeid": "001788FFFE000000", "modelid": "BSB002"}).encode()
        if headers.get("hue-application-key") != KEY:
            return 403, json.dumps({"errors": [{"description": "unauthorized user"}], "data": []}).encode()
        rtype = path.split("/")[3]
        if method == "GET":
            return 200, json.dumps({"errors": [], "data": self.res.get(rtype, [])}).encode()
        rid = path.split("/")[4]
        if not any(r["id"] == rid for r in self.res.get(rtype, [])) and rtype != "grouped_light":
            return 404, json.dumps({"errors": [{"description": "Not Found"}], "data": []}).encode()
        return 200, json.dumps({"errors": [], "data": [{"rid": rid, "rtype": rtype}]}).encode()

    def puts(self):
        return [(url.rsplit("/clip/v2/", 1)[1], body) for method, url, body, _ in self.calls if method == "PUT"]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def test_pair_waits_for_the_link_button_and_returns_the_key():
    bridge, clock = FakeBridge(presses_after=3), Clock()
    out = hue.pair("192.0.2.40", "app#test", transport=bridge, sleep=clock.sleep, clock=clock)
    assert out == {"outcome": "success", "key": KEY, "clientkey": "CK"}
    assert len(bridge.calls) == 4
    assert bridge.calls[0][2] == {"devicetype": "app#test", "generateclientkey": True}


def test_pair_says_the_button_was_not_pressed_when_time_runs_out():
    bridge, clock = FakeBridge(presses_after=999), Clock()
    out = hue.pair("192.0.2.40", transport=bridge, wait=10, every=2, sleep=clock.sleep, clock=clock)
    assert out["outcome"] == "refused"
    assert "link button" in out["why"] and "10 s" in out["why"]
    assert len(bridge.calls) == 6      # at 0, 2, 4, 6, 8 and 10 s


def test_pair_reports_a_bridge_that_does_not_answer():
    def down(*_a):
        raise ConnectionRefusedError()
    clock = Clock()
    out = hue.pair("192.0.2.40", transport=down, wait=4, every=2, sleep=clock.sleep, clock=clock)
    assert out == {"outcome": "failure", "why": "the bridge did not answer (ConnectionRefusedError)"}


def test_config_and_discover():
    bridge = FakeBridge()
    assert hue.config("192.0.2.40", transport=bridge)["modelid"] == "BSB002"

    def service(method, url, body, headers, timeout):
        assert url == hue.DISCOVERY_URL
        return 200, b'[{"id":"001788fffe000000","internalipaddress":"192.0.2.40","port":443}]'
    assert hue.discover(transport=service) == [{"id": "001788fffe000000", "host": "192.0.2.40"}]
    assert hue.discover(transport=lambda *a: (429, b"")) == []


def test_lights_are_summarised_with_reachability():
    lights = {x["name"]: x for x in hue.Bridge("192.0.2.40", KEY, transport=FakeBridge()).lights()}
    assert lights["Desk lamp"] == {"id": "l1", "name": "Desk lamp", "archetype": "classic_bulb", "on": True,
                                   "brightness": 80.0, "color": True, "temperature": True, "reachable": True,
                                   "device": "d1"}
    assert lights["Plant plug"]["brightness"] is None and lights["Plant plug"]["reachable"] is False
    assert lights["Strip"]["reachable"] is None      # the bridge said nothing about it


def test_groups_resolve_room_devices_and_zone_lights():
    groups = {g["name"]: g for g in hue.Bridge("192.0.2.40", KEY, transport=FakeBridge()).groups()}
    assert groups["Living room"] == {"id": "r1", "kind": "room", "name": "Living room", "grouped_light": "g1",
                                     "lights": ["l1", "l2"]}
    assert groups["Top lights"]["lights"] == ["l3"] and groups["Top lights"]["grouped_light"] == "g2"


def test_set_light_by_name_sends_one_put_to_that_light():
    fake = FakeBridge()
    hue.Bridge("192.0.2.40", KEY, transport=fake).set_light("desk LAMP", brightness=40, color="#ffffff")
    [(path, body)] = fake.puts()
    assert path == "resource/light/l1"
    assert body["on"] == {"on": True} and body["dimming"] == {"brightness": 40.0}
    assert body["color"]["xy"] == {"x": 0.3227, "y": 0.329}


def test_brightness_zero_switches_off_and_kelvin_is_clamped():
    fake = FakeBridge()
    b = hue.Bridge("192.0.2.40", KEY, transport=fake)
    b.set_light("l1", brightness=0)
    b.set_light("l1", kelvin=10000)
    assert fake.puts() == [("resource/light/l1", {"on": {"on": False}}),
                           ("resource/light/l1", {"color_temperature": {"mirek": 153}})]


def test_set_group_goes_through_its_grouped_light():
    fake = FakeBridge()
    hue.Bridge("192.0.2.40", KEY, transport=fake).set_group("Living room", on=False)
    assert fake.puts() == [("resource/grouped_light/g1", {"on": {"on": False}})]


def test_a_scene_name_shared_by_two_groups_needs_the_group():
    fake = FakeBridge()
    b = hue.Bridge("192.0.2.40", KEY, transport=fake)
    with pytest.raises(hue.HueError, match="use the id"):
        b.recall_scene("Relax")
    assert b.recall_scene("Relax", group="Top lights")["id"] == "s2"
    assert fake.puts() == [("resource/scene/s2", {"recall": {"action": "active"}})]


def test_unknown_names_and_a_refused_key_are_errors_in_words():
    with pytest.raises(hue.HueError, match="no light called 'Lamp' on the bridge"):
        hue.Bridge("192.0.2.40", KEY, transport=FakeBridge()).set_light("Lamp", on=True)
    with pytest.raises(hue.HueError, match="does not accept this key"):
        hue.Bridge("192.0.2.40", "wrong", transport=FakeBridge()).lights()
    with pytest.raises(ValueError, match="nothing to change"):
        hue.Bridge("192.0.2.40", KEY, transport=FakeBridge()).set_light("l1")


def test_the_key_stays_out_of_repr():
    assert KEY not in repr(hue.Bridge("192.0.2.40", KEY))


def test_hex_to_xy_matches_the_known_primaries():
    assert hue.hex_to_xy("#ff0000") == (0.7006, 0.2993)
    assert hue.hex_to_xy("#0f0") == (0.1724, 0.7468)
    with pytest.raises(ValueError):
        hue.hex_to_xy("red")


def test_cli_pairs_a_hue_device_and_keeps_the_key(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    tokens = tmp_path / "tokens.json"
    cfg.write_text(f'token_file = "{tokens}"\n[devices.lights]\nkind = "hue"\nhost = "192.0.2.40"\n'
                   '[devices.tv]\nkind = "samsung"\nhost = "192.0.2.10"\n')
    fake = FakeBridge()
    monkeypatch.setattr(hue, "_https", fake)
    assert main(["--config", str(cfg), "pair", "lights"]) == 0
    assert json.loads(tokens.read_text()) == {"lights": KEY}
    assert KEY not in capsys.readouterr().out
    assert main(["--config", str(cfg), "hue", "lights", "lights"]) == 0
    assert "Desk lamp: on 80%" in capsys.readouterr().out
    assert main(["--config", str(cfg), "hue", "group", "lights", "Living room", "--off"]) == 0
    assert fake.puts()[-1] == ("resource/grouped_light/g1", {"on": {"on": False}})


def test_cli_state_leaves_a_bridge_out(tmp_path, monkeypatch, capsys):
    from couchctl import power
    cfg = tmp_path / "config.toml"
    cfg.write_text('[devices.lights]\nkind = "hue"\nhost = "192.0.2.40"\n')
    monkeypatch.setattr(power, "state", lambda *a, **k: pytest.fail("a bridge is not a screen"))
    assert main(["--config", str(cfg), "state"]) == 0


def test_discovery_is_verified_and_a_bridge_is_checked_against_the_hue_roots():
    import ssl
    assert hue._discovery_context().verify_mode == ssl.CERT_REQUIRED
    assert hue._discovery_context().check_hostname is True
    bridge = hue._bridge_context()
    assert bridge.verify_mode == ssl.CERT_REQUIRED
    assert bridge.check_hostname is False          # the certificate names the bridge id, not the address
    assert len(bridge.get_ca_certs()) == 2         # both Hue roots are trusted
    assert hue._insecure_context().verify_mode == ssl.CERT_NONE


def test_the_transport_is_chosen_by_url_and_the_insecure_opt_in():
    assert hue._context_for(hue.DISCOVERY_URL).check_hostname is True                  # discovery: full check
    assert hue._context_for("https://192.0.2.40/clip/v2/x").check_hostname is False    # a bridge: chain only
    assert hue._transport_for(None, False) is hue._https
    assert hue._transport_for(None, True) is hue._https_insecure
    mine = lambda *a: (200, b"[]")
    assert hue._transport_for(mine, True) is mine                                      # a caller's own transport wins

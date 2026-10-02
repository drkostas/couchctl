import os
import stat

import pytest

from couchctl import power, redirect, samsung, tokens
from couchctl.config import Redirect

from fake_samsung import FakeTV


@pytest.fixture
def tv():
    t = FakeTV()
    yield t
    t.close()


@pytest.fixture
def token_file(tmp_path):
    return tmp_path / "tokens.json"


def test_info_reads_the_set(tv):
    assert samsung.info(tv.device())["wifiMac"] == "AA:BB:CC:DD:EE:FF"


def test_pairing_keeps_the_token_in_a_private_file(tv, token_file):
    outcome, _ = samsung.pair(tv.device(), token_file)
    assert outcome == "success"
    assert tokens.read(token_file, "tv") == "tok-123456"
    assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600


def test_a_declined_prompt_is_refused(token_file):
    t = FakeTV(allow=False)
    try:
        outcome, _ = samsung.pair(t.device(), token_file)
        assert outcome == "refused"
        assert tokens.read(token_file, "tv") is None
    finally:
        t.close()


def test_keys_need_pairing_first(tv, token_file):
    ok, why = samsung.send_key(tv.device(), token_file, "KEY_HOME")
    assert not ok and "not paired" in why
    samsung.pair(tv.device(), token_file)
    ok, _ = samsung.send_key(tv.device(), token_file, "KEY_HOME")
    assert ok and tv.keys == ["KEY_HOME"]
    assert tv.tokens_seen[-1] == "tok-123456"


def test_power_off_sends_the_power_key_only_when_on(tv, token_file):
    samsung.pair(tv.device(), token_file)
    outcome, _ = power.power_off(tv.device(), token_file)
    assert outcome == "success" and tv.keys == ["KEY_POWER"]


def test_power_off_of_an_unreachable_set_is_refused(tmp_path):
    from couchctl.config import Device

    dev = Device(name="gone", kind="samsung", host="127.0.0.1", api_port=1)
    outcome, why = power.power_off(dev, tmp_path / "t.json")
    assert outcome == "refused" and "already off" in why


def test_state(tv):
    assert power.state(tv.device()) is True
    assert power.state(tv.device(), on_their_network=False) is None


def test_foreground_and_launch(tv):
    dev = tv.device(redirects=[Redirect("app.unwanted", "app.wanted")])
    assert redirect.peek(dev) == (True, None)
    ok, _ = samsung.launch(dev, "app.unwanted")
    assert ok
    assert redirect.peek(dev) == (True, "app.unwanted")


def test_watch_redirects_a_real_press(tv):
    dev = tv.device(redirects=[Redirect("app.unwanted", "app.wanted")])
    steps = iter([None, "press", None])

    def peek(d):
        if next(steps, None) == "press":
            samsung.launch(d, "app.unwanted")  # somebody pressed the button
        return redirect.peek(d)

    done = redirect.watch(dev, polls=2, poll_s=0, peek_fn=peek)
    assert [o["outcome"] for o in done] == ["success"]
    assert tv.launched == ["app.unwanted", "app.wanted"]
    assert tv.apps["app.wanted"]["visible"]


def test_wake_packet():
    pkt = power.wake_packet("aa:bb:cc:dd:ee:ff")
    assert pkt[:6] == b"\xff" * 6 and pkt[6:12] == bytes.fromhex("aabbccddeeff") and len(pkt) == 102
    with pytest.raises(ValueError):
        power.wake_packet("nope")


def test_power_on_without_a_mac_is_refused(tv):
    outcome, _ = power.power_on(tv.device())
    assert outcome == "refused"

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


def test_send_text_types_the_string_and_submit_ends_the_input(tv, token_file):
    ok, why = samsung.send_text(tv.device(), token_file, "hello world")
    assert not ok and "not paired" in why       # a text field needs the same pairing a key does
    samsung.pair(tv.device(), token_file)
    ok, why = samsung.send_text(tv.device(), token_file, "hello world")
    assert ok and why == "text sent"
    assert tv.typed == ["hello world"] and tv.input_ended == 0   # the string, no Done without submit
    ok, why = samsung.send_text(tv.device(), token_file, "搜索 & more", submit=True)
    assert ok and why == "text sent and submitted"
    assert tv.typed == ["hello world", "搜索 & more"]   # base64 carries any unicode unharmed
    assert tv.input_ended == 1                          # submit adds exactly one SendInputEnd


def test_send_keys_presses_each_move_in_order(tv, token_file):
    samsung.pair(tv.device(), token_file)
    ok, _ = samsung.send_keys(tv.device(), token_file, ["left", "down", "ok"], pace=0)
    assert ok and tv.keys == ["KEY_LEFT", "KEY_DOWN", "KEY_ENTER"]


def test_send_keys_with_nothing_opens_no_connection(tv, token_file):
    ok, why = samsung.send_keys(tv.device(), token_file, [], pace=0)
    assert ok and "nothing" in why and tv.keys == []


def test_type_on_keyboard_walks_the_grid_to_the_letters(tv, token_file):
    from couchctl import keyboard
    samsung.pair(tv.device(), token_file)
    ok, why = samsung.type_on_keyboard(tv.device(), token_file, "cat",
                                       keyboard.KEYMAPS["youtube"], pace=0)
    assert ok and "3 characters" in why
    assert tv.keys == [
        "KEY_RIGHT", "KEY_RIGHT", "KEY_ENTER",                       # c
        "KEY_LEFT", "KEY_LEFT", "KEY_ENTER",                         # a
        "KEY_RIGHT", "KEY_RIGHT", "KEY_RIGHT", "KEY_RIGHT", "KEY_RIGHT",
        "KEY_DOWN", "KEY_DOWN", "KEY_ENTER"]                         # t
    assert tv.typed == []   # nothing goes through the set's own keyboard, only remote keys


def test_a_prompt_nobody_answers_is_named_and_no_key_is_sent(token_file, monkeypatch):
    """A set that no longer accepts the held token asks on its screen and, after its prompt time,
    says ms.channel.timeOut. The client must wait past that time to hear it, and must not report
    the key as sent."""
    monkeypatch.setattr(samsung, "PROMPT_SECONDS", 0.5)
    t = FakeTV(unanswered_after=0.5)
    try:
        tokens.write(token_file, "tv", "a-token-the-set-forgot")
        ok, why = samsung.send_key(t.device(), token_file, "KEY_POWER")
        assert not ok and "nobody answered" in why and "--again" in why
        assert t.keys == []
        outcome, _ = power.power_off(t.device(), token_file)
        assert outcome != "success" and t.keys == []
    finally:
        t.close()


def test_pairing_that_nobody_answers_says_so(token_file, monkeypatch):
    monkeypatch.setattr(samsung, "PROMPT_SECONDS", 0.5)
    t = FakeTV(unanswered_after=0.5)
    try:
        outcome, why = samsung.pair(t.device(), token_file)
        assert outcome == "refused" and "nobody answered" in why
        assert tokens.read(token_file, "tv") is None
    finally:
        t.close()


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


@pytest.fixture
def standby():
    t = FakeTV(power_state="standby")
    yield t
    t.close()


def test_a_set_in_standby_is_off_though_it_answers(standby):
    assert power.state(standby.device()) is False


def test_a_set_too_old_to_report_its_power_state_is_on_when_it_answers():
    t = FakeTV(power_state=None)
    try:
        assert power.state(t.device()) is True
    finally:
        t.close()


def test_a_set_that_answers_but_does_not_describe_itself_cannot_be_told(tmp_path):
    import socket

    from couchctl.config import Device

    with socket.socket() as srv:                      # accepts a connection and never answers
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        dev = Device(name="mute", kind="samsung", host="127.0.0.1", api_port=srv.getsockname()[1])
        assert power.state(dev) is None
        outcome, why = power.power_off(dev, tmp_path / "t.json")
        assert outcome == "refused" and "was not sent" in why


def test_power_off_of_a_set_in_standby_sends_no_key(standby, token_file):
    samsung.pair(standby.device(), token_file)
    outcome, why = power.power_off(standby.device(), token_file)
    assert outcome == "refused" and "already off" in why and standby.keys == []


def test_wake_waits_until_a_set_in_standby_says_it_is_on(standby):
    import dataclasses

    sent = []

    def send(mac, address):
        sent.append(address)
        if len(sent) == 4:                            # it comes on with the second packet
            standby.power_state = "on"

    dev = dataclasses.replace(standby.device(), mac="AA:BB:CC:DD:EE:FF")
    outcome, why = power.wake(dev, send=send, sleep=lambda s: None)
    assert outcome == "success" and len(sent) == 4, (outcome, why, sent)


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

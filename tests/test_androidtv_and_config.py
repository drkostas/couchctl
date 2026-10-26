import os
import stat
import textwrap

import pytest

from couchctl import androidtv, redirect
from couchctl.cli import main
from couchctl.config import Device, Redirect, parse

FAKE_ADB = """#!/bin/sh
# A fake adb. It logs every call's arguments to $FAKE_ADB_LOG, and for the type command, which
# reads the text from stdin, it appends that stdin to $FAKE_ADB_STDIN. So a test can prove the text
# arrived on stdin and never in the argument log.
printf '%s\\n' "$*" >> "$FAKE_ADB_LOG"
case "$*" in
  *"read -r t"*) [ -n "$FAKE_ADB_DOWN" ] && exit 1; cat >> "$FAKE_ADB_STDIN" ;;
  *"echo ok"*) [ -n "$FAKE_ADB_DOWN" ] && exit 1; echo ok ;;
  *mResumedActivity*) [ -n "$FAKE_ADB_DOWN" ] && exit 1
     echo "  mResumedActivity: ActivityRecord{1 u0 $FAKE_ADB_FRONT/.Main t1}" ;;
  *monkey*) echo "Events injected: 1" ;;
  *input*) [ -n "$FAKE_ADB_DOWN" ] && exit 1 ;;
esac
exit 0
"""


@pytest.fixture
def adb(tmp_path, monkeypatch):
    path = tmp_path / "adb"
    path.write_text(FAKE_ADB)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "adb.log"
    monkeypatch.setenv("COUCHCTL_ADB", str(path))
    monkeypatch.setenv("FAKE_ADB_LOG", str(log))
    monkeypatch.setenv("FAKE_ADB_STDIN", str(tmp_path / "adb.stdin"))   # what the type command read
    monkeypatch.setenv("FAKE_ADB_FRONT", "com.vendor.home")
    return log


box = Device(name="box", kind="androidtv", adb="10.0.0.9:5555", redirects=[Redirect("com.unwanted", "com.wanted")])


def test_foreground_reads_the_package(adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_FRONT", "com.unwanted")
    assert androidtv.foreground(box) == (True, "com.unwanted")
    assert redirect.peek(box) == (True, "com.unwanted")


def test_an_unanswering_box_is_unreachable_and_gets_a_reconnect(adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_DOWN", "1")
    assert redirect.peek(box) == (False, None)
    assert "connect 10.0.0.9:5555" in adb.read_text()


def test_state_of_an_android_device_comes_from_adb_not_a_tcp_connection(adb, monkeypatch):
    from couchctl import power

    assert power.state(box) is True
    monkeypatch.setenv("FAKE_ADB_DOWN", "1")
    assert power.state(box) is False


def test_launch_uses_the_tv_launcher_category(adb):
    ok, _ = androidtv.launch(box, "com.wanted")
    assert ok
    assert "monkey -p com.wanted -c android.intent.category.LEANBACK_LAUNCHER 1" in adb.read_text()


def test_input_arg_sends_spaces_as_percent_s():
    assert androidtv._input_arg("a b & c(1)") == "a%sb%s&%sc(1)"   # only spaces change; stdin carries the rest
    assert androidtv._input_arg("p@ss w0rd!") == "p@ss%sw0rd!"


def test_send_text_goes_on_stdin_not_the_argument_list(adb):
    # the whole point of the fix: text typed here (a password included) must never reach any argv
    ok, why = androidtv.send_text(box, "hunter2 & co", submit=True)
    assert ok and why == "text sent and submitted"
    args = adb.read_text()
    assert "hunter2" not in args                                 # the words are not on the command line
    assert 'IFS= read -r t; input text "$t"' in args             # the command that reads from stdin
    assert (adb.parent / "adb.stdin").read_text() == "hunter2%s&%sco\n"   # the text arrived on stdin
    assert "input keyevent KEYCODE_ENTER" in args


def test_send_text_without_submit_sends_no_enter(adb):
    ok, why = androidtv.send_text(box, "hi there")
    assert ok and why == "text sent"
    assert (adb.parent / "adb.stdin").read_text() == "hi%sthere\n"
    assert "KEYCODE_ENTER" not in adb.read_text()


def test_send_text_to_a_device_that_does_not_answer_fails(adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_DOWN", "1")
    ok, why = androidtv.send_text(box, "hi")
    assert not ok and "did not answer" in why


def test_send_keys_presses_each_dpad_key_over_adb(adb):
    ok, why = androidtv.send_keys(box, ["left", "down", "ok"], pace=0)
    assert ok
    log = adb.read_text()
    assert "input keyevent KEYCODE_DPAD_LEFT" in log
    assert "input keyevent KEYCODE_DPAD_DOWN" in log
    assert "input keyevent KEYCODE_DPAD_CENTER" in log


def test_send_keys_with_nothing_touches_adb_not_at_all(adb):
    ok, why = androidtv.send_keys(box, [], pace=0)
    assert ok and "nothing" in why
    assert not adb.exists()           # no adb call was made at all


def test_type_on_keyboard_walks_the_grid_over_adb(adb):
    from couchctl import keyboard
    ok, why = androidtv.type_on_keyboard(box, "cat", keyboard.KEYMAPS["youtube"], pace=0)
    assert ok and "3 characters" in why
    log = adb.read_text()
    assert log.count("input keyevent KEYCODE_DPAD_CENTER") == 3   # a select per letter
    assert "input keyevent KEYCODE_DPAD_RIGHT" in log and "input keyevent KEYCODE_DPAD_DOWN" in log


def test_send_keys_stops_when_the_device_stops_answering(adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_DOWN", "1")
    ok, why = androidtv.send_keys(box, ["left", "ok"], pace=0)
    assert not ok and "did not answer" in why


def test_config_parses_and_defaults_the_adb_port():
    cfg = parse({"devices": {
        "tv": {"kind": "samsung", "host": "10.0.0.2", "redirect": [{"from": "1", "to": "2", "name": "a to b"}]},
        "box": {"kind": "androidtv", "host": "10.0.0.3"},
    }})
    assert cfg.device("box").adb == "10.0.0.3:5555"
    assert cfg.device("tv").redirect_for("1").target == "2"
    with pytest.raises(KeyError):
        cfg.device("nope")


def test_config_problems_are_listed_together():
    with pytest.raises(ValueError) as e:
        parse({"devices": {"a": {"kind": "roku"}, "b": {"kind": "samsung"}, "c": {"kind": "samsung", "host": "h", "redirect": [{"from": "1"}]}}})
    assert str(e.value).count("\n  - ") == 3


def test_example_config_is_valid():
    from couchctl.config import load

    here = os.path.dirname(__file__)
    cfg = load(os.path.join(here, "..", "examples", "config.example.toml"))
    assert cfg.devices


def test_cli_state_with_a_config(tmp_path, capsys):
    conf = tmp_path / "c.toml"
    conf.write_text(textwrap.dedent("""
        [devices.gone]
        kind = "samsung"
        host = "127.0.0.1"
        api_port = 1
    """))
    assert main(["--config", str(conf), "state"]) == 0
    assert capsys.readouterr().out.strip() == "gone: off"
    assert main(["--config", str(conf), "state", "--maybe-away"]) == 0
    assert "unknown" in capsys.readouterr().out
    assert main(["--config", str(conf), "off", "nope"]) == 2


def test_cli_text_on_an_androidtv_device(tmp_path, adb, capsys):
    conf = tmp_path / "c.toml"
    conf.write_text(textwrap.dedent("""
        [devices.box]
        kind = "androidtv"
        adb = "10.0.0.9:5555"
    """))
    assert main(["--config", str(conf), "text", "box", "find me", "--enter"]) == 0
    assert capsys.readouterr().out.strip() == "text sent and submitted"
    log = adb.read_text()
    assert "find" not in log                                              # the text is on stdin, not in argv
    assert (adb.parent / "adb.stdin").read_text() == "find%sme\n"
    assert 'IFS= read -r t; input text "$t"' in log and "input keyevent KEYCODE_ENTER" in log

import os
import stat
import textwrap

import pytest

from couchctl import androidtv, redirect
from couchctl.cli import main
from couchctl.config import Device, Redirect, parse

FAKE_ADB = """#!/bin/sh
# A fake adb. It logs every call and answers from $FAKE_ADB_FRONT.
echo "$@" >> "$FAKE_ADB_LOG"
case "$*" in
  *"echo ok"*) [ -n "$FAKE_ADB_DOWN" ] && exit 1; echo ok ;;
  *mResumedActivity*) [ -n "$FAKE_ADB_DOWN" ] && exit 1
     echo "  mResumedActivity: ActivityRecord{1 u0 $FAKE_ADB_FRONT/.Main t1}" ;;
  *monkey*) echo "Events injected: 1" ;;
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

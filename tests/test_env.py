"""An env given to the adb functions reaches adb, and adb itself is found on that env's PATH."""
import os
import stat
import subprocess

from couchctl import androidtv, power, redirect
from couchctl.config import Device


def _fake_adb(tmp_path):
    adb = tmp_path / "adb"
    adb.write_text("#!/bin/sh\necho ok\n")
    adb.chmod(adb.stat().st_mode | stat.S_IEXEC)
    return adb


def _capture(monkeypatch):
    seen = []

    def fake(cmd, **k):
        seen.append((cmd[0], k.get("env")))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(subprocess, "run", fake)
    return seen


def test_adb_is_found_on_the_given_path(tmp_path, monkeypatch):
    adb = _fake_adb(tmp_path)
    monkeypatch.delenv("COUCHCTL_ADB", raising=False)
    assert androidtv.adb_path({"PATH": str(tmp_path)}) == str(adb)
    assert androidtv.adb_path({"PATH": "/nonexistent"}) is None


def test_the_env_reaches_every_adb_call(tmp_path, monkeypatch):
    adb = _fake_adb(tmp_path)
    env = {"PATH": str(tmp_path)}
    seen = _capture(monkeypatch)
    dev = Device(name="box", kind="androidtv", adb="10.0.0.2:5555")
    assert power.state(dev, env=env) is True
    redirect.peek(dev, env=env)
    assert seen and all(cmd == str(adb) and e is env for cmd, e in seen)


def test_no_env_inherits(tmp_path, monkeypatch):
    _fake_adb(tmp_path)
    monkeypatch.setenv("COUCHCTL_ADB", str(tmp_path / "adb"))
    seen = _capture(monkeypatch)
    androidtv.shell("10.0.0.2:5555", "echo ok")
    assert seen == [(os.environ["COUCHCTL_ADB"], None)]
